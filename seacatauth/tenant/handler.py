import logging

import asab.web.rest
import asab.exceptions

from .. import exceptions
from ..decorators import access_control

###

L = logging.getLogger(__name__)

###


class TenantHandler(object):

	def __init__(self, app, tenant_svc):
		self.TenantService = tenant_svc
		self.NameProposerService = app.get_service("seacatauth.NameProposerService")

		web_app = app.WebContainer.WebApp
		web_app.router.add_get('/tenant', self.list)
		web_app.router.add_get('/tenants', self.search)
		web_app.router.add_get('/tenant/{tenant}', self.get)
		web_app.router.add_put('/tenant/{tenant}', self.update_tenant)
		web_app.router.add_put('/tenants', self.get_tenants_batch)

		web_app.router.add_post('/tenant', self.create)
		web_app.router.add_delete('/tenant/{tenant}', self.delete)

		web_app.router.add_get('/tenant_assign/{credentials_id}', self.get_tenants_by_credentials)
		web_app.router.add_put('/tenant_assign/{credentials_id}', self.set_tenants)
		web_app.router.add_post('/tenant_assign/{credentials_id}/{tenant}', self.assign_tenant)
		web_app.router.add_delete('/tenant_assign/{credentials_id}/{tenant}', self.unassign_tenant)

		web_app.router.add_put("/tenant_assign_many", self.bulk_assign_tenants)
		web_app.router.add_put("/tenant_unassign_many", self.bulk_unassign_tenants)

		web_app.router.add_get('/tenant_propose', self.propose_tenant)

		# Public endpoints
		web_app_public = app.PublicWebContainer.WebApp
		web_app_public.router.add_get('/tenant', self.list)


	# IMPORTANT: This endpoint needs to be compatible with `/tenant` handler in Asab Tenant Service
	async def list(self, request):
		# TODO: This has to be cached agressivelly
		provider = self.TenantService.get_provider()
		result = []
		async for tenant in provider.iterate():
			result.append(tenant['_id'])
		return asab.web.rest.json_response(request, data=result)


	async def search(self, request):
		page = int(request.query.get("p", 1)) - 1
		limit = request.query.get("i")
		if limit is not None:
			limit = int(limit)

		filter = request.query.get("f", "")
		if len(filter) == 0:
			filter = None

		provider = self.TenantService.get_provider()

		count = await provider.count(filter=filter)

		tenants = []
		async for tenant in provider.iterate(page, limit, filter):
			tenants.append(tenant)

		result = {
			"result": "OK",
			"data": tenants,
			"count": count,
		}

		return asab.web.rest.json_response(request, data=result)


	async def get(self, request):
		tenant_id = request.match_info.get("tenant")
		data = await self.TenantService.get_tenant(tenant_id)
		return asab.web.rest.json_response(request, data)


	@asab.web.rest.json_schema_handler({
		"type": "object",
		"properties": {
			"id": {"type": "string"},
		},
		"required": ["id"],
		"additionalProperties": False,
	})
	@access_control("authz:superuser")
	async def create(self, request, *, credentials_id, json_data):
		tenant_id = json_data["id"]

		# Create tenant
		result = await self.TenantService.create_tenant(tenant_id, creator_id=credentials_id)

		return asab.web.rest.json_response(
			request,
			data=result,
			status=200 if result["result"] == "OK" else 400
		)

	@asab.web.rest.json_schema_handler({
		"type": "object",
		"additionalProperties": False,
		"properties": {
			"description": {
				"type": "string"},
			"data": {
				"type": "object",
				"patternProperties": {
					"^[a-zA-Z][a-zA-Z0-9_-]{0,126}[a-zA-Z0-9]$": {"anyOf": [
						{"type": "string"},
						{"type": "number"},
						{"type": "boolean"},
						{"type": "null"},
					]}
				}
			}
		}
	})
	@access_control("authz:tenant:admin")
	async def update_tenant(self, request, *, json_data, tenant):
		result = await self.TenantService.update_tenant(tenant, **json_data)
		return asab.web.rest.json_response(request, data=result)


	@access_control("authz:superuser")
	async def delete(self, request, *, tenant):
		"""
		Delete a tenant. Also delete all its roles and assignments linked to this tenant.
		"""
		result = await self.TenantService.delete_tenant(tenant)
		return asab.web.rest.json_response(request, data=result)


	@asab.web.rest.json_schema_handler({
		"type": "object",
		"required": [
			"tenants",
		],
		"properties": {
			"tenants": {
				"type": "array",
				"items": {
					"type": "string",
				},
			},
		}
	})
	@access_control()
	async def set_tenants(self, request, *, json_data):
		"""
		Helper method for bulk tenant un/assignment
		"""
		credentials_id = request.match_info["credentials_id"]
		data = await self.TenantService.set_tenants(
			session=request.Session,
			credentials_id=credentials_id,
			tenants=json_data["tenants"]
		)

		return asab.web.rest.json_response(
			request,
			data=data,
			status=200 if data["result"] == "OK" else 400
		)


	@access_control("authz:tenant:admin")
	async def assign_tenant(self, request, *, tenant):
		await self.TenantService.assign_tenant(
			request.match_info["credentials_id"],
			tenant,
		)
		return asab.web.rest.json_response(request, data={"result": "OK"})


	@access_control("authz:tenant:admin")
	async def unassign_tenant(self, request, *, tenant):
		await self.TenantService.unassign_tenant(
			request.match_info["credentials_id"],
			tenant,
		)
		return asab.web.rest.json_response(request, data={"result": "OK"})


	async def get_tenants_by_credentials(self, request):
		result = await self.TenantService.get_tenants(request.match_info["credentials_id"])
		return asab.web.rest.json_response(
			request, result
		)


	@asab.web.rest.json_schema_handler({
		"type": "array",
		"items": {"type": "string"}
	})
	async def get_tenants_batch(self, request, *, json_data):
		response = {
			cid: await self.TenantService.get_tenants(cid)
			for cid in json_data
		}
		return asab.web.rest.json_response(request, response)


	async def propose_tenant(self, request):
		proposed_tenant = self.NameProposerService.propose_name()
		# TODO: Check is the proposed tenant name is not already taken
		return asab.web.rest.json_response(request, {'tenant_id': proposed_tenant})


	@asab.web.rest.json_schema_handler({
		"type": "object",
		"required": ["credential_ids", "tenants"],
		"properties": {
			"credential_ids": {
				"type": "array",
				"items": {"type": "string"}},
			"tenants": {
				"type": "object",
				"patternProperties": {
					r"^\*$|^[a-z][a-z0-9._-]{2,31}$": {
						"type": "array",
						"items": {"type": "string"}}}}}
	})
	@access_control("authz:superuser")
	async def bulk_assign_tenants(self, request, *, json_data):
		"""
		Grant tenant access and/or assign roles to a list of credentials.
		---
		security:
		- oAuth:
			- authz:superuser
		"""
		credential_service = self.TenantService.App.get_service("seacatauth.CredentialsService")
		role_service = self.TenantService.App.get_service("seacatauth.RoleService")

		# Validate that all the credentials exist
		for credential_id in json_data["credential_ids"]:
			try:
				await credential_service.detail(credential_id)
			except KeyError:
				raise asab.exceptions.ValidationError("Credentials not found: {}".format(credential_id))

		# Validate that tenants and their roles exists
		for tenant, roles in json_data["tenants"].items():
			if tenant != "*":
				try:
					await self.TenantService.get(tenant)
				except KeyError:
					raise asab.exceptions.ValidationError("Tenant not found: {}".format(tenant))
			for role in roles:
				t, _ = role.split("/", 1)
				if t != tenant:
					# Role is not listed under its proper tenant
					raise asab.exceptions.ValidationError("Role {!r} not found in tenant {!r}".format(role, tenant))
				try:
					await role_service.get(role)
				except KeyError:
					raise asab.exceptions.ValidationError("Role not found: {}".format(role))

		error_details = []
		for tenant, roles in json_data["tenants"].items():
			for credential_id in json_data["credential_ids"]:
				success = False
				try:
					await self.TenantService.assign_tenant(
						credential_id, tenant, verify_tenant=False, verify_credentials=False)
					success = True
				except asab.exceptions.Conflict:
					L.info("Skipping: Tenant already assigned.", struct_data={
						"cid": credential_id, "tenant": tenant})
					success = True
				except Exception as e:
					L.error("Cannot assign tenant: {}".format(e), exc_info=True, struct_data={
						"cid": credential_id, "tenant": tenant})
					error_details.append({"cid": credential_id, "tenant": tenant})
				if not success:
					continue

				if len(roles) == 0:
					continue

				for role in roles:
					try:
						await role_service.assign_role(
							credential_id, role, verify_role=False, verify_credentials=False, verify_tenant=False)
					except asab.exceptions.Conflict:
						L.info("Skipping: Role already assigned.", struct_data={
							"cid": credential_id, "role": role})
					except Exception as e:
						L.error("Cannot assign role: {}".format(e), exc_info=True, struct_data={
							"cid": credential_id, "role": role})
						error_details.append({"cid": credential_id, "role": role})

		data = {
			"error_count": len(error_details),
			"error_details": error_details,
			"result": "OK"}
		return asab.web.rest.json_response(request, data=data)

	@asab.web.rest.json_schema_handler({
		"type": "object",
		"required": ["credential_ids", "tenants"],
		"properties": {
			"credential_ids": {
				"type": "array",
				"items": {"type": "string"}},
			"tenants": {
				"type": "object",
				"patternProperties": {
					r"^\*$|^[a-z][a-z0-9._-]{2,31}$": {
						"type": "array",
						"items": {"type": "string"}}}}}
	})
	@access_control("authz:superuser")
	async def bulk_unassign_tenants(self, request, *, json_data):
		"""
		Revoke tenant access and/or unassign roles from a list of credentials.
		---
		security:
		- oAuth:
			- authz:superuser
		"""
		role_service = self.TenantService.App.get_service("seacatauth.RoleService")

		# Verify that roles are listed under their proper tenant
		for tenant, roles in json_data["tenants"].items():
			for role in roles:
				t, _ = role.split("/", 1)
				if t != tenant:
					raise asab.exceptions.ValidationError("Role {!r} not found in tenant {!r}".format(role, tenant))

		error_details = []
		for tenant, roles in json_data["tenants"].items():
			for credential_id in json_data["credential_ids"]:
				if len(roles) == 0:
					# If no roles are listed under the tenant (e.g. `"my-tenant": []`),
					# revoke access to the tenant completely.
					# This also automatically unassigns all the tenant's roles
					try:
						await self.TenantService.unassign_tenant(credential_id, tenant)
					except Exception as e:
						L.error("Cannot unassign tenant: {}".format(e), exc_info=True, struct_data={
							"cid": credential_id, "tenant": tenant})
						error_details.append({"cid": credential_id, "tenant": tenant})
				else:
					# If any roles are listed under the tenant (e.g. `"my-tenant": ["my-tenant/user"]`),
					# unassign only those and keep the tenant access.
					for role in roles:
						try:
							await role_service.unassign_role(credential_id, role)
						except Exception as e:
							L.error("Cannot unassign role: {}".format(e), exc_info=True, struct_data={
								"cid": credential_id, "role": role})
							error_details.append({"cid": credential_id, "role": role})

		data = {
			"successful_count": successful_count,
			"error_count": len(error_details),
			"error_details": error_details,
			"result": "OK"}
		return asab.web.rest.json_response(request, data=data)
