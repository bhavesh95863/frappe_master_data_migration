frappe.ui.form.on("Party Opening Balance Import", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.status !== "Posted") {
			frm.add_custom_button(__("Fetch Balances"), () => {
				frm.call("fetch_balances").then((r) => {
					if (!r.exc) {
						frappe.show_alert({
							message: __("Fetched {0} party balances", [r.message]),
							indicator: "green",
						});
						frm.reload_doc();
					}
				});
			});
		}

		if (frm.doc.status === "Fetched" && (frm.doc.items || []).length) {
			frm.add_custom_button(__("Post Opening Entry"), () => {
				frappe.confirm(
					__("Create and submit an Opening Entry Journal Entry for all mapped rows?"),
					() => {
						frm.call("post_opening_entry").then((r) => {
							if (!r.exc) {
								frappe.show_alert({
									message: __("Created {0}", [r.message]),
									indicator: "green",
								});
								frm.reload_doc();
							}
						});
					}
				);
			}).addClass("btn-primary");
		}

		if (frm.doc.status === "Posted" && frm.doc.created_entries) {
			frm.add_custom_button(__("Open Journal Entry"), () => {
				frappe.set_route("Form", "Journal Entry", frm.doc.created_entries);
			});
		}
	},

	party_type(frm) {
		// Keep each row's Dynamic Link target in sync with the chosen party type.
		(frm.doc.items || []).forEach((row) => {
			frappe.model.set_value(row.doctype, row.name, "party_doctype", frm.doc.party_type);
		});
	},
});
