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
