"""Thin HTTP client for calling the source site's export API."""

import json

import requests

import frappe
from frappe import _

API_PREFIX = "/api/method/frappe_master_data_migration.api."


class RemoteClient:
	def __init__(self, connection):
		self.base_url = (connection.remote_url or "").rstrip("/")
		key = connection.api_key
		secret = connection.get_password("api_secret")
		self.headers = {
			"Authorization": f"token {key}:{secret}",
			"Accept": "application/json",
		}

	def call(self, method: str, params: dict | None = None) -> object:
		url = self.base_url + API_PREFIX + method
		payload = {k: _encode(v) for k, v in (params or {}).items()}
		try:
			response = requests.post(url, headers=self.headers, data=payload, timeout=(10, 90))
		except requests.RequestException as exc:
			frappe.throw(_("Could not reach {0}: {1}").format(self.base_url, exc))

		if response.status_code >= 400:
			frappe.throw(_("Source returned {0}: {1}").format(response.status_code, response.text[:500]))

		return response.json().get("message")


def _encode(value):
	if isinstance(value, (dict, list)):
		return json.dumps(value)
	return value
