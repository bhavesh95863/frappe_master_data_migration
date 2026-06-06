frappe.ui.form.on("Migration Connection", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frm.call("test_connection")
				.then((r) => {
					if (!r.exc) {
						frappe.show_alert({ message: __("Connection OK"), indicator: "green" });
						frm.reload_doc();
					}
				});
		});
	},
});
