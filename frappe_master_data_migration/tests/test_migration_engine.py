from types import SimpleNamespace

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from frappe_master_data_migration import migration_engine as engine


class TestFieldMapping(UnitTestCase):
	def test_apply_to_row(self):
		mappings = [
			{"target_fieldname": "company", "map_type": "Replace Value", "from_value": "Old", "to_value": "New"},
			{"target_fieldname": "currency", "map_type": "Set Fixed Value", "from_value": None, "to_value": "USD"},
			{"target_fieldname": "notes", "map_type": "Clear Value", "from_value": None, "to_value": None},
		]
		row = {"company": "Old", "currency": "INR", "notes": "x", "keep": "me"}
		engine._apply_to_row(mappings, row)
		self.assertEqual(row, {"company": "New", "currency": "USD", "notes": None, "keep": "me"})

	def test_replace_only_on_match(self):
		mappings = [{"target_fieldname": "company", "map_type": "Replace Value", "from_value": "Old", "to_value": "New"}]
		row = {"company": "Something Else"}
		engine._apply_to_row(mappings, row)
		self.assertEqual(row["company"], "Something Else")

	def test_format_missing(self):
		missing = [{"fieldname": "company", "option": "Company", "value": "Acme"}]
		self.assertEqual(engine._format_missing(missing), "Missing links: company=Acme")


class TestScopedMapping(IntegrationTestCase):
	def test_scope_limits_rule_to_child_table(self):
		doc = {"title": "Parent", "seen_by": [{"user": "alice"}, {"user": "bob"}]}
		mappings = [
			{"child_table": "seen_by", "target_fieldname": "user", "map_type": "Set Fixed Value", "to_value": "Administrator"},
			{"child_table": "", "target_fieldname": "title", "map_type": "Set Fixed Value", "to_value": "Changed"},
		]
		engine._apply_mappings_to("Note", mappings, doc)
		self.assertEqual(doc["title"], "Changed")
		self.assertTrue(all(row["user"] == "Administrator" for row in doc["seen_by"]))

	def test_global_rule_hits_parent_and_children(self):
		doc = {"user": "x", "seen_by": [{"user": "y"}]}
		mappings = [{"child_table": "", "target_fieldname": "user", "map_type": "Set Fixed Value", "to_value": "Administrator"}]
		engine._apply_mappings_to("Note", mappings, doc)
		self.assertEqual(doc["user"], "Administrator")
		self.assertEqual(doc["seen_by"][0]["user"], "Administrator")


class TestLinkResolution(UnitTestCase):
	def test_map_replaces_value(self):
		ctx = SimpleNamespace(
			resolutions={("", "company", "Acme Pvt"): {"action": "Map", "map_to": "Acme Ltd", "link_doctype": "Company"}},
			created_cache=set(),
		)
		row = {"company": "Acme Pvt"}
		engine._resolve_row(ctx, "", [{"fieldname": "company", "options": "Company"}], row)
		self.assertEqual(row["company"], "Acme Ltd")

	def test_keep_leaves_value(self):
		ctx = SimpleNamespace(resolutions={}, created_cache=set())
		row = {"company": "Acme Pvt"}
		engine._resolve_row(ctx, "", [{"fieldname": "company", "options": "Company"}], row)
		self.assertEqual(row["company"], "Acme Pvt")


class TestStop(IntegrationTestCase):
	def test_should_stop_reads_status(self):
		conn = frappe.get_doc(
			{
				"doctype": "Migration Connection",
				"connection_name": "Stop Test",
				"remote_url": "http://localhost",
				"api_key": "k",
				"api_secret": "s",
			}
		).insert()
		job = frappe.get_doc({"doctype": "Migration Job", "connection": conn.name, "source_doctype": "Gender"}).insert()
		self.assertFalse(engine._should_stop(job.name))
		job.db_set("status", "Stopping")
		self.assertTrue(engine._should_stop(job.name))


class TestInsert(IntegrationTestCase):
	def test_insert_preserves_name_and_children(self):
		note = frappe.get_doc(
			{"doctype": "Note", "title": "MDM Engine Test", "public": 1, "seen_by": [{"user": "Administrator"}]}
		).insert()
		name = note.name
		data = note.as_dict(no_nulls=True)
		frappe.delete_doc("Note", name, force=True)

		engine._insert_new("Note", name, data, ignore_links=True)

		created = frappe.get_doc("Note", name)
		self.assertEqual(created.name, name)
		self.assertEqual(len(created.seen_by), 1)
