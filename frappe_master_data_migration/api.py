"""Source-side export API.

Runs on the site that *holds* the data. The destination site authenticates with an
API key/secret (Frappe's built-in `Authorization: token key:secret`) and calls these
methods to read DocType metadata and export records with optional files, comments,
versions, assignments and tags.

Every method re-checks per-DocType read permission so an API key can only export what
its user is already allowed to read.
"""

import base64
import json

import frappe
from frappe import _

# Linked via the Dynamic Link "links" child table (point back to the parent), not by a
# direct Link field — migrated together with the parent, kept out of the link analyzer.
RELATED_DOCTYPES = ("Address", "Contact")


@frappe.whitelist()
def ping() -> dict:
	return {
		"site": frappe.local.site,
		"user": frappe.session.user,
		"version": frappe.__version__,
	}


@frappe.whitelist()
def get_doctype_meta(doctype: str) -> dict:
	_check_read(doctype)
	meta = frappe.get_meta(doctype)
	return {
		"doctype": doctype,
		"child_tables": _child_tables(meta),
		"link_fields": _link_fields(meta),
	}


@frappe.whitelist()
def list_record_names(
	doctype: str,
	filters: str | None = None,
	modified_after: str | None = None,
	start: int = 0,
	limit: int = 100,
) -> dict:
	_check_read(doctype)
	parsed = _parse_filters(filters)
	if modified_after:
		parsed.append(["modified", ">", modified_after])

	rows = frappe.get_all(
		doctype,
		filters=parsed,
		fields=["name"],
		order_by="creation asc",
		limit_start=int(start),
		limit_page_length=int(limit) + 1,
	)
	names = [row["name"] for row in rows]
	has_next = len(names) > int(limit)
	return {"names": names[: int(limit)], "has_next": has_next}


@frappe.whitelist()
def export_records(
	doctype: str,
	names: str | list,
	with_files: int = 0,
	with_comments: int = 0,
	with_versions: int = 0,
	with_assignments_tags: int = 0,
) -> list:
	_check_read(doctype)
	names = _as_list(names)
	return [
		_export_one(doctype, name, int(with_files), int(with_comments), int(with_versions), int(with_assignments_tags))
		for name in names
	]


@frappe.whitelist()
def analyze_links(doctype: str, filters: str | None = None, child_fieldnames: str | list | None = None) -> list:
	_check_read(doctype)
	parsed = _parse_filters(filters)
	result = []

	for field in frappe.get_meta(doctype).get_link_fields():
		if field.options in RELATED_DOCTYPES:
			continue
		values = frappe.get_all(doctype, filters=parsed, pluck=field.fieldname, distinct=True)
		_add_link_row(result, "", field.fieldname, field.options, values)

	parent_names = _parent_names(doctype, parsed) if parsed else None
	for child_fieldname in _as_list(child_fieldnames or []):
		_analyze_child_links(result, doctype, child_fieldname, parent_names)

	return result


def _analyze_child_links(result, doctype, child_fieldname, parent_names):
	table_field = frappe.get_meta(doctype).get_field(child_fieldname)
	if not table_field or table_field.fieldtype != "Table":
		return
	child_doctype = table_field.options
	child_filters = {"parenttype": doctype, "parentfield": child_fieldname}
	if parent_names is not None:
		child_filters["parent"] = ["in", parent_names]
	for field in frappe.get_meta(child_doctype).get_link_fields():
		if field.options in RELATED_DOCTYPES:
			continue
		values = frappe.get_all(child_doctype, filters=child_filters, pluck=field.fieldname, distinct=True)
		_add_link_row(result, child_fieldname, field.fieldname, field.options, values)


def _add_link_row(result, child_table, link_field, link_doctype, values):
	clean = sorted({value for value in values if value})
	if clean:
		result.append({"child_table": child_table, "link_field": link_field, "link_doctype": link_doctype, "values": clean})


def _parent_names(doctype, parsed):
	return frappe.get_all(doctype, filters=parsed, pluck="name")


@frappe.whitelist()
def export_linked_documents(parent_doctype: str, parent_names: str | list) -> dict:
	"""Addresses/Contacts that point back to each parent via the Dynamic Link 'links' table."""
	_check_read(parent_doctype)
	names = _as_list(parent_names)
	result = {name: [] for name in names}
	for related in RELATED_DOCTYPES:
		_collect_related(result, related, parent_doctype, names)
	return result


def _collect_related(result, related_doctype, parent_doctype, names):
	if not frappe.has_permission(related_doctype, "read"):
		return
	links = frappe.get_all(
		"Dynamic Link",
		filters={
			"parenttype": related_doctype,
			"parentfield": "links",
			"link_doctype": parent_doctype,
			"link_name": ["in", names],
		},
		fields=["parent", "link_name"],
	)
	cache = {}
	for row in links:
		related_name = row["parent"]
		if related_name not in cache:
			doc = frappe.get_doc(related_doctype, related_name)
			cache[related_name] = {"doctype": related_doctype, "name": related_name, "doc": doc.as_dict(no_nulls=True)}
		result[row["link_name"]].append(cache[related_name])


def _export_one(doctype, name, with_files, with_comments, with_versions, with_assignments_tags):
	doc = frappe.get_doc(doctype, name)
	payload = {"name": name, "doc": doc.as_dict(no_nulls=True)}
	if with_files:
		payload["files"] = _get_files(doctype, name)
	if with_comments:
		payload["comments"] = _get_comments(doctype, name)
	if with_versions:
		payload["versions"] = _get_versions(doctype, name)
	if with_assignments_tags:
		payload["assignments"] = _get_assignments(doc)
		payload["tags"] = _get_tags(doc)
	return payload


def _get_files(doctype, name):
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": doctype, "attached_to_name": name},
		fields=["name", "file_name", "is_private", "creation", "owner"],
	)
	result = []
	for entry in files:
		content = _read_file_content(entry["name"])
		if content is None:
			continue
		result.append(
			{
				"file_name": entry["file_name"],
				"is_private": entry["is_private"],
				"content_base64": base64.b64encode(content).decode(),
				"creation": str(entry["creation"]),
				"owner": entry["owner"],
			}
		)
	return result


def _read_file_content(file_name):
	try:
		content = frappe.get_doc("File", file_name).get_content()
	except Exception:
		frappe.clear_last_message()
		return None
	return content.encode("utf-8") if isinstance(content, str) else content


def _get_comments(doctype, name):
	return frappe.get_all(
		"Comment",
		filters={"reference_doctype": doctype, "reference_name": name, "comment_type": "Comment"},
		fields=["content", "comment_email", "comment_by", "creation", "owner"],
		order_by="creation asc",
	)


def _get_versions(doctype, name):
	return frappe.get_all(
		"Version",
		filters={"ref_doctype": doctype, "docname": name},
		fields=["data", "owner", "creation"],
		order_by="creation asc",
	)


def _get_assignments(doc):
	return frappe.parse_json(doc.get("_assign") or "[]")


def _get_tags(doc):
	tags = doc.get("_user_tags") or ""
	return [tag for tag in tags.split(",") if tag]


def _child_tables(meta):
	tables = []
	for field in meta.get_table_fields():
		tables.append(
			{
				"fieldname": field.fieldname,
				"child_doctype": field.options,
				"label": _(field.label or field.fieldname),
			}
		)
	return tables


def _link_fields(meta):
	fields = []
	for field in meta.get("fields", {"fieldtype": "Link"}):
		fields.append({"fieldname": field.fieldname, "options": field.options})
	return fields


def _parse_filters(filters):
	if not filters:
		return []
	parsed = json.loads(filters) if isinstance(filters, str) else filters
	return parsed if parsed else []


def _as_list(value):
	if isinstance(value, str):
		return json.loads(value)
	return value


def _check_read(doctype: str):
	if not frappe.has_permission(doctype, "read"):
		frappe.throw(_("Not permitted to read {0}").format(doctype), frappe.PermissionError)
