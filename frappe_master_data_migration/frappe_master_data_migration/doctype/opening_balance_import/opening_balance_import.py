import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import cint, flt

from frappe_master_data_migration.remote_client import RemoteClient


class OpeningBalanceImport(Document):
	def validate(self):
		for row in self.items:
			row.stock_qty = flt(row.qty) * flt(row.conversion_factor or 1)
			self._set_tracking_flags(row)

	def _set_tracking_flags(self, row):
		"""Mirror the target item's batch/serial flags onto the row so the grid can show
		and gate the batch field. Recomputed on every save as target_item changes."""
		if row.target_item:
			has_batch, has_serial = frappe.db.get_value(
				"Item", row.target_item, ["has_batch_no", "has_serial_no"]
			) or (0, 0)
			row.target_has_batch = cint(has_batch)
			row.target_has_serial = cint(has_serial)
		else:
			row.target_has_batch = 0
			row.target_has_serial = 0

	def _client(self):
		connection = frappe.get_doc("Migration Connection", self.connection)
		return RemoteClient(connection)

	@frappe.whitelist()
	def fetch_balances(self):
		"""Pull item balances from the source as of the date and seed the mapping grid."""
		if self.status == "Posted":
			frappe.throw(_("This import has already been posted."))
		if not self.as_of_date:
			frappe.throw(_("Set an 'As of Date' first."))

		rows = self._client().call(
			"export_item_balances",
			{
				"as_of_date": str(self.as_of_date),
				"warehouse": self.source_warehouse_filter or None,
				"item_group": self.item_group_filter or None,
			},
		)

		self.items = []
		for row in rows or []:
			source_uom = row.get("stock_uom")
			self.append(
				"items",
				{
					"source_item_code": row.get("item_code"),
					"source_item_name": row.get("item_name"),
					"source_warehouse": row.get("warehouse"),
					"source_uom": source_uom,
					"source_qty": flt(row.get("balance_qty")),
					"source_valuation_rate": flt(row.get("valuation_rate")),
					"action": "Map",
					# Smart defaults — an untouched row is a straight 1:1 import.
					"target_item": row.get("item_code") if frappe.db.exists("Item", row.get("item_code")) else None,
					"target_warehouse": self._match_warehouse(row.get("warehouse")),
					"target_uom": source_uom if frappe.db.exists("UOM", source_uom) else None,
					"conversion_factor": 1,
					"qty": flt(row.get("balance_qty")),
					"valuation_rate": flt(row.get("valuation_rate")),
				},
			)

		self.status = "Fetched" if self.items else "Draft"
		self.save()
		return len(self.items)

	def _match_warehouse(self, source_warehouse):
		"""Exact name match only — warehouse names usually carry a company abbr suffix,
		so anything fuzzier would map to the wrong company. Left blank for the user otherwise."""
		if source_warehouse and frappe.db.exists("Warehouse", source_warehouse):
			return source_warehouse
		return None

	@frappe.whitelist()
	def post_opening_stock(self):
		"""Create & submit one Stock Reconciliation (Opening Stock) from the mapped rows."""
		if self.status == "Posted":
			frappe.throw(_("This import has already been posted."))

		mapped = [row for row in self.items if row.action == "Map"]
		if not mapped:
			frappe.throw(_("No rows are set to Map."))

		self._validate_rows(mapped)
		if self.save_uom_conversions:
			self._save_uom_conversions(mapped)

		recon = frappe.new_doc("Stock Reconciliation")
		recon.company = self.company
		recon.purpose = "Opening Stock"
		recon.set_posting_time = 1
		recon.posting_date = self.as_of_date
		recon.posting_time = "23:59:59"
		if self.default_expense_account:
			recon.expense_account = self.default_expense_account

		for row in mapped:
			recon.append(
				"items",
				{
					"item_code": row.target_item,
					"warehouse": row.target_warehouse,
					"qty": flt(row.stock_qty),
					"valuation_rate": flt(row.valuation_rate),
					"allow_zero_valuation_rate": 1 if flt(row.valuation_rate) <= 0 else 0,
					**self._tracking_fields(row),
				},
			)

		recon.insert()
		recon.submit()

		for row in mapped:
			row.db_set("mapped", 1, update_modified=False)

		self.db_set("created_entries", recon.name)
		self.db_set("status", "Posted")
		frappe.db.commit()
		return recon.name

	def _validate_rows(self, rows):
		errors = []
		for row in rows:
			if not row.target_item:
				errors.append(_("Row {0}: Target Item is required.").format(row.idx))
			if not row.target_warehouse:
				errors.append(_("Row {0}: Target Warehouse is required.").format(row.idx))
			if flt(row.stock_qty) <= 0:
				errors.append(_("Row {0}: Stock Qty must be greater than zero.").format(row.idx))
		if errors:
			frappe.throw("<br>".join(errors))

	def _tracking_fields(self, row):
		"""Build batch/serial fields for a Stock Reconciliation row when the target item is
		tracked here. The source had no batches/serials, so opening ones are created fresh:
		one batch per item+warehouse and serials generated from the item's series."""
		item = frappe.get_doc("Item", row.target_item)
		if not (item.has_batch_no or item.has_serial_no):
			return {}

		fields = {"use_serial_batch_fields": 1}
		if item.has_batch_no:
			fields["batch_no"] = self._ensure_batch(item, row)
		if item.has_serial_no:
			fields["serial_no"] = "\n".join(self._generate_serials(item, row))
		return fields

	def _ensure_batch(self, item, row):
		"""Reuse an existing batch number if the user typed one, else auto-create a single
		opening batch (named from the item's batch series or by ERPNext's fallback)."""
		if row.batch_no and frappe.db.exists("Batch", row.batch_no):
			return row.batch_no
		if not row.batch_no and not item.create_new_batch:
			frappe.throw(
				_("Row {0}: Item {1} is batch-tracked without auto-batch creation. Enter an Opening Batch No.").format(
					row.idx, item.name
				)
			)
		batch = frappe.new_doc("Batch")
		batch.item = item.name
		if row.batch_no:
			batch.batch_id = row.batch_no
		batch.insert()
		row.db_set("batch_no", batch.name, update_modified=False)
		return batch.name

	def _generate_serials(self, item, row):
		qty = cint(row.stock_qty)
		if flt(row.stock_qty) != qty:
			frappe.throw(
				_("Row {0}: Serial item {1} needs a whole-number stock qty (got {2}).").format(
					row.idx, item.name, row.stock_qty
				)
			)
		if not item.serial_no_series:
			frappe.throw(
				_("Row {0}: Serial item {1} has no Serial No Series to generate opening serials from.").format(
					row.idx, item.name
				)
			)
		return [make_autoname(item.serial_no_series, "Serial No") for _ in range(qty)]

	def _save_uom_conversions(self, rows):
		"""Write target UOM → stock UOM factors into each Item, skipping UOMs that already
		exist with a different factor (reported, never overwritten)."""
		conflicts = []
		for row in rows:
			if not row.target_uom or flt(row.conversion_factor) in (0, 1):
				continue
			item = frappe.get_doc("Item", row.target_item)
			if row.target_uom == item.stock_uom:
				continue
			existing = next((u for u in item.uoms if u.uom == row.target_uom), None)
			if existing:
				if flt(existing.conversion_factor) != flt(row.conversion_factor):
					conflicts.append(
						_("{0}: UOM {1} already set to {2} (kept), row has {3}").format(
							row.target_item, row.target_uom, existing.conversion_factor, row.conversion_factor
						)
					)
				continue
			item.append("uoms", {"uom": row.target_uom, "conversion_factor": flt(row.conversion_factor)})
			item.save()

		if conflicts:
			frappe.msgprint(
				_("Some UOM conversions were left unchanged:") + "<br>" + "<br>".join(conflicts),
				title=_("UOM Conversion Conflicts"),
				indicator="orange",
			)
