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

	@frappe.whitelist()
	def analyze_links(self):
		if not self.source_doctype:
			frappe.throw(_("Set a Source DocType first"))

		included = [row.fieldname for row in self.child_tables if row.include]
		groups = self._client().call(
			"analyze_links",
			{"doctype": self.source_doctype, "filters": self.filters_json or "", "child_fieldnames": included},
		)
		self._sync_link_resolutions(groups)
		self.save()
		return len(self.link_resolutions)

	@frappe.whitelist()
	def get_progress(self):
		total = self.total_fetched or 0
		processed = self.processed_count or 0
		return {
			"status": self.status,
			"total": total,
			"processed": processed,
			"percent": round(processed / total * 100) if total else 0,
			"created": self.created_count,
			"updated": self.updated_count,
			"skipped": self.skipped_count,
			"failed": self.failed_count,
		}

	@frappe.whitelist(methods=["POST"])
	def reset_status(self):
		"""Recover a job left stuck in Queued/Running/Stopping by a worker that died."""
		self.db_set("status", "Draft", update_modified=False)
		frappe.db.commit()
		return self.status

	@frappe.whitelist(methods=["POST"])
	def stop_migration(self):
		if self.status not in ("Queued", "Running"):
			frappe.throw(_("Nothing to stop — migration is {0}").format(self.status))
		self.db_set("status", "Stopping", update_modified=False)
		frappe.db.commit()
		return self.status

	def on_trash(self):
		if self.status in ("Queued", "Running", "Stopping"):
			frappe.throw(_("Stop the migration before deleting this job"))
		frappe.db.delete("Migration Record Log", {"migration_job": self.name})

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
			migration_job=self.name,
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

	def _sync_link_resolutions(self, groups):
		prior = {(r.child_table, r.link_field, r.source_value): (r.action, r.map_to) for r in self.link_resolutions}
		self.link_resolutions = []
		for group in groups:
			for value in group["values"]:
				exists = bool(frappe.db.exists(group["link_doctype"], value))
				action, map_to = prior.get(
					(group["child_table"], group["link_field"], value),
					("Keep" if exists else "Create New", None),
				)
				self.append(
					"link_resolutions",
					{
						"child_table": group["child_table"],
						"link_field": group["link_field"],
						"link_doctype": group["link_doctype"],
						"source_value": value,
						"exists": exists,
						"action": action,
						"map_to": map_to,
					},
				)

	def _reset_results(self):
		fields = ("total_fetched", "processed_count", "created_count", "updated_count", "skipped_count", "failed_count")
		for field in fields:
			self.set(field, 0)
		self.run_log = ""
