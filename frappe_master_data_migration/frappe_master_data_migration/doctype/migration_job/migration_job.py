import frappe
from frappe import _
from frappe.model.document import Document

from frappe_master_data_migration.remote_client import RemoteClient


class MigrationJob(Document):
	@frappe.whitelist()
	def test_connection(self):
		return self._client().call("ping")

	@frappe.whitelist()
	def fetch_meta(self):
		if not self.source_doctype:
			frappe.throw(_("Set a Source DocType first"))

		meta = self._client().call("get_doctype_meta", {"doctype": self.source_doctype})
		self._sync_child_tables(meta.get("child_tables") or [])
		self.save()
		return meta

	@frappe.whitelist(methods=["POST"])
	def start_migration(self):
		if self.status == "Running":
			frappe.throw(_("Migration is already running"))

		self._reset_results()
		self.status = "Queued"
		self.save()

		frappe.enqueue(
			"frappe_master_data_migration.migration_engine.run_migration",
			queue="long",
			timeout=3600,
			enqueue_after_commit=True,
			job_id=f"mdm:{self.name}",
			deduplicate=True,
			job_name=self.name,
		)
		return self.status

	def _client(self):
		connection = frappe.get_doc("Migration Connection", self.connection)
		return RemoteClient(connection)

	def _sync_child_tables(self, child_tables):
		existing = {row.fieldname: row.include for row in self.child_tables}
		self.child_tables = []
		for table in child_tables:
			self.append(
				"child_tables",
				{
					"fieldname": table["fieldname"],
					"child_doctype": table["child_doctype"],
					"label": table.get("label") or table["fieldname"],
					"include": existing.get(table["fieldname"], 1),
				},
			)

	def _reset_results(self):
		for field in ("total_fetched", "created_count", "updated_count", "skipped_count", "failed_count"):
			self.set(field, 0)
		self.run_log = ""
