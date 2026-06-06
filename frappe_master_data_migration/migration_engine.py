"""Destination-side migration engine.

Pulls records from the source site (via RemoteClient) and creates/updates them locally,
applying value-mapping rules, child-table selection and link-resolution policy. Runs in a
background worker, enqueued from Migration Job.start_migration.
"""

import frappe
from frappe import _

from frappe_master_data_migration.remote_client import RemoteClient

PAGE_SIZE = 200
EXPORT_BATCH = 50

SYSTEM_FIELDS = {
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"idx",
	"_user_tags",
	"_comments",
	"_assign",
	"_liked_by",
}
CHILD_SYSTEM_FIELDS = {
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"parent",
	"parentfield",
	"parenttype",
	"docstatus",
}


class JobContext:
	def __init__(self, job):
		self.job = job
		self.doctype = job.source_doctype
		self.client = RemoteClient(frappe.get_doc("Migration Connection", job.connection))
		self.excluded_child_fields = {row.fieldname for row in job.child_tables if not row.include}
		self.mappings = [
			{
				"target_fieldname": row.target_fieldname,
				"map_type": row.map_type,
				"from_value": row.from_value,
				"to_value": row.to_value,
			}
			for row in job.field_mappings
		]
		self.link_fields = [
			{"fieldname": field.fieldname, "options": field.options}
			for field in frappe.get_meta(self.doctype).get_link_fields()
		]
		self.counts = {"Created": 0, "Updated": 0, "Skipped": 0, "Failed": 0}


def run_migration(job_name: str | None = None):
	job = frappe.get_doc("Migration Job", job_name)
	job.db_set("status", "Running")
	try:
		_run(job)
	except Exception:
		job.db_set("status", "Failed")
		job.db_set("run_log", frappe.get_traceback())
		raise


def _run(job):
	frappe.db.delete("Migration Record Log", {"migration_job": job.name})
	ctx = JobContext(job)
	names = _fetch_all_names(ctx)
	job.db_set("total_fetched", len(names))

	done = 0
	for batch in _batches(names, EXPORT_BATCH):
		for record in _export(ctx, batch):
			_import_record(ctx, record)
			done += 1
		_save_counts(ctx)
		_publish(job.name, done, len(names))
		frappe.db.commit()

	_finalize(ctx)


def _import_record(ctx, record):
	savepoint = "mdm_rec"
	frappe.db.savepoint(savepoint)
	try:
		action, message = _import_one(ctx, record)
		_log_record(ctx.job, record["name"], action, message)
		ctx.counts[action] += 1
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		_log_record(ctx.job, record["name"], "Failed", frappe.get_traceback(with_context=False)[:2000])
		ctx.counts["Failed"] += 1


def _import_one(ctx, record):
	name = record["name"]
	doc_dict = record["doc"]
	_strip_excluded_children(ctx, doc_dict)
	_apply_mappings(ctx, doc_dict)

	missing = _missing_links(ctx, doc_dict)
	if missing and ctx.job.missing_link_action == "Skip Record":
		return "Skipped", _format_missing(missing)

	exists = frappe.db.exists(ctx.doctype, name)
	if exists and ctx.job.on_conflict == "Skip":
		return "Skipped", "Already exists"

	if missing and ctx.job.missing_link_action == "Auto-create Stub":
		_create_stubs(missing)

	ignore_links = ctx.job.missing_link_action != "Skip Record"
	if exists:
		_update_existing(ctx.doctype, name, doc_dict, ignore_links)
		return "Updated", ""

	_insert_new(ctx.doctype, name, doc_dict, ignore_links)
	_import_extras(ctx, name, record)
	note = _format_missing(missing) if missing else ""
	return "Created", note


def _insert_new(doctype, name, doc_dict, ignore_links):
	doc = frappe.get_doc(_prepare(doc_dict, doctype, name))
	doc.flags.name_set = True
	doc.name = name
	doc.insert(ignore_permissions=True, ignore_links=ignore_links)


def _update_existing(doctype, name, doc_dict, ignore_links):
	doc = frappe.get_doc(doctype, name)
	doc.update(_prepare(doc_dict, doctype, name, for_update=True))
	doc.flags.ignore_links = ignore_links
	doc.save(ignore_permissions=True)


def _prepare(doc_dict, doctype, name, for_update=False):
	data = dict(doc_dict)
	data["doctype"] = doctype
	for key in SYSTEM_FIELDS:
		data.pop(key, None)
	if for_update:
		data.pop("name", None)
	else:
		data["name"] = name
	_clean_children(data)
	return data


def _clean_children(data):
	meta = frappe.get_meta(data["doctype"])
	for field in meta.get_table_fields():
		for row in data.get(field.fieldname) or []:
			for key in CHILD_SYSTEM_FIELDS:
				row.pop(key, None)


def _strip_excluded_children(ctx, doc_dict):
	for fieldname in ctx.excluded_child_fields:
		doc_dict.pop(fieldname, None)


def _apply_mappings(ctx, doc_dict):
	if not ctx.mappings:
		return
	_apply_to_row(ctx.mappings, doc_dict)
	for field in frappe.get_meta(ctx.doctype).get_table_fields():
		for row in doc_dict.get(field.fieldname) or []:
			_apply_to_row(ctx.mappings, row)


def _apply_to_row(mappings, row):
	for rule in mappings:
		field = rule["target_fieldname"]
		if field not in row:
			continue
		if rule["map_type"] == "Set Fixed Value":
			row[field] = rule["to_value"]
		elif rule["map_type"] == "Clear Value":
			row[field] = None
		elif str(row.get(field)) == str(rule["from_value"]):
			row[field] = rule["to_value"]


def _missing_links(ctx, doc_dict):
	missing = []
	for field in ctx.link_fields:
		value = doc_dict.get(field["fieldname"])
		if value and not frappe.db.exists(field["options"], value):
			missing.append({"fieldname": field["fieldname"], "option": field["options"], "value": value})
	return missing


def _create_stubs(missing):
	for link in missing:
		_create_stub(link["option"], link["value"])


def _create_stub(doctype, name):
	if frappe.db.exists(doctype, name):
		return
	meta = frappe.get_meta(doctype)
	doc = frappe.new_doc(doctype)
	autoname = meta.autoname or ""
	if autoname.startswith("field:"):
		doc.set(autoname.split(":", 1)[1], name)
	if meta.title_field:
		doc.set(meta.title_field, name)
	doc.flags.ignore_mandatory = True
	doc.flags.name_set = True
	doc.name = name
	doc.insert(ignore_permissions=True, ignore_links=True)


def _import_extras(ctx, name, record):
	if ctx.job.include_files:
		_import_files(ctx.doctype, name, record.get("files") or [])
	if ctx.job.include_comments:
		_import_comments(ctx.doctype, name, record.get("comments") or [])
	if ctx.job.include_versions:
		_import_versions(ctx.doctype, name, record.get("versions") or [])
	if ctx.job.include_assignments_tags:
		_import_assignments(ctx.doctype, name, record.get("assignments") or [])
		_import_tags(ctx.doctype, name, record.get("tags") or [])


def _import_files(doctype, name, files):
	for entry in files:
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": entry["file_name"],
				"attached_to_doctype": doctype,
				"attached_to_name": name,
				"is_private": entry.get("is_private") or 0,
				"content": entry["content_base64"],
				"decode": True,
			}
		).insert(ignore_permissions=True)


def _import_comments(doctype, name, comments):
	for comment in comments:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": doctype,
				"reference_name": name,
				"content": comment.get("content"),
				"comment_email": comment.get("comment_email"),
				"comment_by": comment.get("comment_by"),
			}
		).insert(ignore_permissions=True)


def _import_versions(doctype, name, versions):
	for version in versions:
		frappe.get_doc(
			{"doctype": "Version", "ref_doctype": doctype, "docname": name, "data": version.get("data")}
		).insert(ignore_permissions=True)


def _import_assignments(doctype, name, users):
	from frappe.desk.form.assign_to import add as assign_add

	for user in users:
		try:
			assign_add({"assign_to": [user], "doctype": doctype, "name": name})
		except Exception:
			frappe.clear_last_message()


def _import_tags(doctype, name, tags):
	from frappe.desk.doctype.tag.tag import add_tag

	for tag in tags:
		try:
			add_tag(tag, doctype, name)
		except Exception:
			frappe.clear_last_message()


def _fetch_all_names(ctx):
	names = []
	start = 0
	cap = ctx.job.record_limit or 0
	while True:
		result = ctx.client.call(
			"list_record_names",
			{
				"doctype": ctx.doctype,
				"filters": ctx.job.filters_json or "",
				"modified_after": str(ctx.job.modified_after or ""),
				"start": start,
				"limit": PAGE_SIZE,
			},
		)
		names.extend(result["names"])
		if cap and len(names) >= cap:
			return names[:cap]
		if not result["has_next"]:
			return names
		start += PAGE_SIZE


def _export(ctx, batch):
	return ctx.client.call(
		"export_records",
		{
			"doctype": ctx.doctype,
			"names": batch,
			"with_files": int(ctx.job.include_files),
			"with_comments": int(ctx.job.include_comments),
			"with_versions": int(ctx.job.include_versions),
			"with_assignments_tags": int(ctx.job.include_assignments_tags),
		},
	)


def _log_record(job, source_name, action, message):
	frappe.get_doc(
		{
			"doctype": "Migration Record Log",
			"migration_job": job.name,
			"source_name": source_name,
			"action": action,
			"message": (message or "")[:2000],
		}
	).insert(ignore_permissions=True)


def _save_counts(ctx):
	for action, count in ctx.counts.items():
		ctx.job.db_set(f"{action.lower()}_count", count, update_modified=False)


def _finalize(ctx):
	_save_counts(ctx)
	status = "Completed with Errors" if ctx.counts["Failed"] else "Completed"
	summary = ", ".join(f"{action}: {count}" for action, count in ctx.counts.items())
	ctx.job.db_set("status", status)
	ctx.job.db_set("run_log", summary)
	frappe.db.commit()


def _publish(job_name, done, total):
	frappe.publish_realtime("mdm_progress", {"job": job_name, "done": done, "total": total})


def _batches(items, size):
	for index in range(0, len(items), size):
		yield items[index : index + size]


def _format_missing(missing):
	return "Missing links: " + ", ".join(f"{m['fieldname']}={m['value']}" for m in missing)
