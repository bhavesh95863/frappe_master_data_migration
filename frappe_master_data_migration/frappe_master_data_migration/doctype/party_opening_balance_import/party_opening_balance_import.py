import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from frappe_master_data_migration.remote_client import RemoteClient


class PartyOpeningBalanceImport(Document):
	def validate(self):
		for row in self.items:
			row.party_doctype = self.party_type

	def _client(self):
		connection = frappe.get_doc("Migration Connection", self.connection)
		return RemoteClient(connection)

	@frappe.whitelist()
	def fetch_balances(self):
		"""Pull party balances from the source as of the date and seed the mapping grid."""
		if self.status == "Posted":
			frappe.throw(_("This import has already been posted."))
		if not self.party_type:
			frappe.throw(_("Select a Party Type first."))
		if not self.as_of_date:
			frappe.throw(_("Set an 'As of Date' first."))

		rows = self._client().call(
			"export_party_balances",
			{
				"party_type": self.party_type,
				"as_of_date": str(self.as_of_date),
				"company": self.source_company or None,
			},
		)

		self.items = []
		for row in rows or []:
			party = row.get("party")
			self.append(
				"items",
				{
					"source_party": party,
					"source_party_name": row.get("party_name"),
					"balance": flt(row.get("balance")),
					"action": "Map",
					"party_doctype": self.party_type,
					"target_party": party if frappe.db.exists(self.party_type, party) else None,
					"amount": flt(row.get("balance")),
				},
			)

		self.status = "Fetched" if self.items else "Draft"
		self.save()
		return len(self.items)

	@frappe.whitelist()
	def post_opening_entry(self):
		"""Create & submit one Opening Entry Journal Entry from the mapped party balances."""
		if self.status == "Posted":
			frappe.throw(_("This import has already been posted."))

		mapped = [row for row in self.items if row.action == "Map"]
		if not mapped:
			frappe.throw(_("No rows are set to Map."))
		self._validate_rows(mapped)

		temp_account = self._temp_account()
		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Opening Entry"
		je.is_opening = "Yes"
		je.company = self.company
		je.posting_date = self.as_of_date

		total_debit = 0.0
		total_credit = 0.0
		for row in mapped:
			amount = flt(row.amount)
			line = {
				"account": self._party_account(row),
				"party_type": self.party_type,
				"party": row.target_party,
			}
			if self.cost_center:
				line["cost_center"] = self.cost_center
			if amount >= 0:
				line["debit_in_account_currency"] = amount
				total_debit += amount
			else:
				line["credit_in_account_currency"] = -amount
				total_credit += -amount
			je.append("accounts", line)

		# Single balancing line against the Temporary Opening account.
		net = total_debit - total_credit
		balancing = {"account": temp_account}
		if self.cost_center:
			balancing["cost_center"] = self.cost_center
		if net >= 0:
			balancing["credit_in_account_currency"] = net
		else:
			balancing["debit_in_account_currency"] = -net
		je.append("accounts", balancing)

		je.insert()
		je.submit()

		for row in mapped:
			row.db_set("mapped", 1, update_modified=False)
		self.db_set("created_entries", je.name)
		self.db_set("status", "Posted")
		frappe.db.commit()
		return je.name

	def _validate_rows(self, rows):
		errors = []
		for row in rows:
			if not row.target_party:
				errors.append(_("Row {0}: Target Party is required.").format(row.idx))
			if flt(row.amount) == 0:
				errors.append(_("Row {0}: Opening Amount cannot be zero.").format(row.idx))
		if errors:
			frappe.throw("<br>".join(errors))

	def _party_account(self, row):
		account = row.target_party_account or self.default_party_account
		if not account:
			from erpnext.accounts.party import get_party_account

			account = get_party_account(self.party_type, row.target_party, self.company)
		if not account:
			frappe.throw(
				_("Row {0}: No receivable/payable account for {1}. Set a Default Party Account.").format(
					row.idx, row.target_party
				)
			)
		return account

	def _temp_account(self):
		if self.temporary_opening_account:
			return self.temporary_opening_account
		account = frappe.db.get_value(
			"Account", {"company": self.company, "account_type": "Temporary", "is_group": 0}, "name"
		)
		if not account:
			frappe.throw(_("Set a Temporary Opening Account — none of type 'Temporary' found for {0}.").format(self.company))
		return account
