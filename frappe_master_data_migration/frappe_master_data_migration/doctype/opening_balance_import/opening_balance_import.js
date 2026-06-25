frappe.ui.form.on("Opening Balance Import", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.status !== "Posted") {
			frm.add_custom_button(__("Fetch Balances"), () => {
				frm.call("fetch_balances").then((r) => {
					if (!r.exc) {
						frappe.show_alert({
							message: __("Fetched {0} item balances", [r.message]),
							indicator: "green",
						});
						frm.reload_doc();
					}
				});
			});
		}

		if (frm.doc.status === "Fetched" && (frm.doc.items || []).length) {
			frm.add_custom_button(__("Post Opening Stock"), () => {
				frappe.confirm(
					__("Create and submit a Stock Reconciliation (Opening Stock) for all mapped rows?"),
					() => {
						frm.call("post_opening_stock").then((r) => {
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
			frm.add_custom_button(__("Open Stock Reconciliation"), () => {
				frappe.set_route("Form", "Stock Reconciliation", frm.doc.created_entries);
			});
		}
	},
});

frappe.ui.form.on("Opening Balance Import Item", {
	qty: recompute,
	conversion_factor: recompute,
	target_item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.target_item) {
			frappe.model.set_value(cdt, cdn, "target_has_batch", 0);
			frappe.model.set_value(cdt, cdn, "target_has_serial", 0);
			return;
		}
		frappe.db.get_value("Item", row.target_item, ["has_batch_no", "has_serial_no"]).then((r) => {
			const v = r.message || {};
			frappe.model.set_value(cdt, cdn, "target_has_batch", v.has_batch_no || 0);
			frappe.model.set_value(cdt, cdn, "target_has_serial", v.has_serial_no || 0);
		});
	},
});

function recompute(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "stock_qty", flt(row.qty) * flt(row.conversion_factor || 1));
}
