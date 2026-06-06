frappe.ui.form.on("Migration Job", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		const busy = ["Queued", "Running", "Stopping"].includes(frm.doc.status);

		frm.add_custom_button(__("Fetch Meta"), () => fetch_meta(frm));
		frm.add_custom_button(__("Analyze Links"), () => analyze_links(frm));

		if (!busy) {
			frm.add_custom_button(__("Start Migration"), () => start_migration(frm)).addClass(
				"btn-primary"
			);
		} else {
			frm.add_custom_button(__("Stop Migration"), () => stop_migration(frm)).addClass(
				"btn-danger"
			);
		}

		frm.add_custom_button(__("View Results"), () => {
			frappe.set_route("List", "Migration Record Log", { migration_job: frm.doc.name });
		});

		if (["Running", "Queued"].includes(frm.doc.status)) {
			frm.dashboard.set_headline(__("Migration {0}…", [frm.doc.status]));
		}

		subscribe_progress(frm);
	},
});

frappe.ui.form.on("Migration Field Map", {
	target_fieldname: (frm, cdt, cdn) => set_value_doctype(frm, cdt, cdn),
	child_table: (frm, cdt, cdn) => set_value_doctype(frm, cdt, cdn),
	to_value_link: (frm, cdt, cdn) => {
		frappe.model.set_value(cdt, cdn, "to_value", locals[cdt][cdn].to_value_link);
	},
});

async function set_value_doctype(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row.target_fieldname) {
		return;
	}

	let doctype = frm.doc.source_doctype;
	if (row.child_table) {
		const table = (frm.doc.child_tables || []).find((r) => r.fieldname === row.child_table);
		doctype = table ? table.child_doctype : null;
	}
	if (!doctype) {
		return;
	}

	await frappe.model.with_doctype(doctype);
	const df = frappe.get_meta(doctype).fields.find((f) => f.fieldname === row.target_fieldname);
	if (df && df.fieldtype === "Link") {
		frappe.model.set_value(cdt, cdn, "target_doctype", df.options);
	}
}

function fetch_meta(frm) {
	frappe.dom.freeze(__("Fetching metadata from source…"));
	frm.call("fetch_meta")
		.then((r) => {
			if (!r.exc) {
				frappe.show_alert({ message: __("Metadata loaded"), indicator: "green" });
				frm.reload_doc();
			}
		})
		.always(() => frappe.dom.unfreeze());
}

function analyze_links(frm) {
	frappe.dom.freeze(__("Analyzing link values in the source…"));
	frm.call("analyze_links")
		.then((r) => {
			if (!r.exc) {
				frappe.show_alert({ message: __("Found {0} distinct link values", [r.message]), indicator: "green" });
				frm.reload_doc();
			}
		})
		.always(() => frappe.dom.unfreeze());
}

function stop_migration(frm) {
	frappe.confirm(__("Stop this migration? It will halt after the current batch."), () => {
		frm.call("stop_migration").then((r) => {
			if (!r.exc) {
				frappe.show_alert({ message: __("Stopping…"), indicator: "orange" });
				frm.reload_doc();
			}
		});
	});
}

function start_migration(frm) {
	frappe.confirm(__("Start pulling {0} records from the source?", [frm.doc.source_doctype]), () => {
		frm.call("start_migration").then((r) => {
			if (!r.exc) {
				frappe.show_alert({ message: __("Migration queued"), indicator: "blue" });
				frm.reload_doc();
			}
		});
	});
}

function subscribe_progress(frm) {
	frappe.realtime.off("mdm_progress");
	frappe.realtime.on("mdm_progress", (data) => {
		if (data.job !== frm.doc.name) {
			return;
		}
		frm.dashboard.show_progress(
			__("Migration"),
			(data.done / (data.total || 1)) * 100,
			__("{0} of {1} records", [data.done, data.total])
		);
		if (data.done >= data.total) {
			setTimeout(() => frm.reload_doc(), 1500);
		}
	});
}
