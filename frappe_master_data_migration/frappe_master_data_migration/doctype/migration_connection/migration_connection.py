import frappe
from frappe.model.document import Document

from frappe_master_data_migration.remote_client import RemoteClient


class MigrationConnection(Document):
	@frappe.whitelist()
	def test_connection(self):
		client = RemoteClient(self)
		info = client.call("ping")
		self.db_set("last_tested", frappe.utils.now_datetime())
		self.db_set("last_status", f"OK — {info.get('site')} ({info.get('version')}) as {info.get('user')}")
		return info
