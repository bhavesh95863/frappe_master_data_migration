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
			frm.add_custom_button(__("Reset (force)"), () => reset_status(frm));
		}

		frm.add_custom_button(__("View Results"), () => {
			frappe.set_route("List", "Migration Record Log", { migration_job: frm.doc.name });
		});

		render_progress(frm);
	},
});

const BUSY = ["Queued", "Running", "Stopping"];

function render_progress(frm) {
	stop_poll(frm);
	frappe.realtime.off("mdm_progress");
	if (frm.is_new()) {
		return;
	}

	const counts = {
		created: frm.doc.created_count,
		updated: frm.doc.updated_count,
		skipped: frm.doc.skipped_count,
		failed: frm.doc.failed_count,
	};
	draw_progress(frm, frm.doc.processed_count, frm.doc.total_fetched, counts, frm.doc.status);

	if (!BUSY.includes(frm.doc.status)) {
		return;
	}

	frappe.realtime.on("mdm_progress", (data) => {
		if (data.job === frm.doc.name) {
			draw_progress(frm, data.done, data.total, null, "Running");
		}
	});
	frm.__mdm_poll = setInterval(() => poll_progress(frm), 3000);
}

function poll_progress(frm) {
	if (frappe.get_route()[1] !== "Migration Job" || frappe.get_route()[2] !== frm.doc.name) {
		stop_poll(frm);
		return;
	}
	frm.call("get_progress").then((r) => {
		if (r.exc || !r.message) {
			return;
		}
		const p = r.message;
		draw_progress(frm, p.processed, p.total, p, p.status);
		if (!BUSY.includes(p.status)) {
			stop_poll(frm);
			frm.reload_doc();
			frappe.show_alert({ message: __("Migration {0}", [p.status]), indicator: "blue" });
		}
	});
}

function draw_progress(frm, processed, total, counts, status) {
	processed = processed || 0;
	total = total || 0;
	const percent = total ? Math.min(100, Math.round((processed / total) * 100)) : 0;
	let message = __("{0} of {1} records ({2}%)", [processed, total, percent]);
	if (counts) {
		message += __(" — created {0}, updated {1}, skipped {2}, failed {3}", [
			counts.created || 0,
			counts.updated || 0,
			counts.skipped || 0,
			counts.failed || 0,
		]);
	}
	frm.dashboard.show_progress(__("Migration ({0})", [status]), percent || 0.5, message);
	const indicator = status === "Failed" ? "red" : BUSY.includes(status) ? "orange" : "green";
	frm.dashboard.set_headline(`<span class="indicator ${indicator}">${__(status)} — ${percent}%</span>`);
}

function stop_poll(frm) {
	if (frm.__mdm_poll) {
		clearInterval(frm.__mdm_poll);
		frm.__mdm_poll = null;
	}
}

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

function reset_status(frm) {
	frappe.confirm(
		__("Force-reset this job to Draft? Only do this if it's stuck (the worker died). It does not stop a worker that is still running."),
		() => {
			frm.call("reset_status").then((r) => {
				if (!r.exc) {
					frappe.show_alert({ message: __("Reset to Draft"), indicator: "blue" });
					frm.reload_doc();
				}
			});
		}
	);
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
