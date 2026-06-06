frappe.ui.form.on("Migration Job", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Fetch Meta"), () => fetch_meta(frm));

		if (frm.doc.status !== "Running" && frm.doc.status !== "Queued") {
			frm.add_custom_button(__("Start Migration"), () => start_migration(frm)).addClass(
				"btn-primary"
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
