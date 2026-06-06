frappe.listview_settings["Migration Job"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const colors = {
			Draft: "gray",
			Queued: "blue",
			Running: "orange",
			Stopping: "orange",
			Stopped: "yellow",
			Completed: "green",
			"Completed with Errors": "yellow",
			Failed: "red",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
