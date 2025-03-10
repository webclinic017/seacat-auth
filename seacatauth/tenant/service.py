import logging
import re

import asab
import asab.storage.exceptions
import asab.exceptions

from .. import exceptions

#

L = logging.getLogger(__name__)

#


class TenantService(asab.Service):
	TenantNamePattern = r"[a-z][a-z0-9._-]{2,31}"

	def __init__(self, app, service_name="seacatauth.TenantService"):
		super().__init__(app, service_name)
		self.TenantsProvider = None
		self.TenantNameRegex = re.compile("^{}$".format(self.TenantNamePattern))
		self.AuditService = app.get_service("seacatauth.AuditService")


	def create_provider(self, provider_id, config_section_name):
		assert (self.TenantsProvider is None)  # We support only one tenant provider for now
		_, creds, provider_type, provider_name = config_section_name.rsplit(":", 3)
		if provider_type == 'mongodb':
			from .providers.mongodb import MongoDBTenantProvider
			provider = MongoDBTenantProvider(self.App, provider_id, config_section_name)

		else:
			raise RuntimeError("Unsupported tenant provider '{}'".format(provider_type))

		self.TenantsProvider = provider


	async def get_tenant(self, tenant_id: str):
		return await self.TenantsProvider.get(tenant_id)


	async def create_tenant(self, tenant_id: str, creator_id: str = None):
		if not self.TenantNameRegex.match(tenant_id):
			raise asab.exceptions.ValidationError(
				"Invalid tenant ID {!r}. "
				"Tenant ID must consist only of characters 'a-z0-9._-', "
				"start with a letter, and be between 3 and 32 characters long.".format(tenant_id))

		try:
			tenant_id = await self.TenantsProvider.create(tenant_id, creator_id)
		except asab.storage.exceptions.DuplicateError:
			L.error("Tenant with this ID already exists.", struct_data={"tenant": tenant_id})
			raise asab.exceptions.Conflict(value=tenant_id)

		return tenant_id


	async def update_tenant(self, tenant_id: str, **kwargs):
		result = await self.TenantsProvider.update(tenant_id, **kwargs)
		return {"result": result}


	async def delete_tenant(self, tenant_id: str):
		session_service = self.App.get_service("seacatauth.SessionService")

		# Unassign and delete tenant roles
		role_svc = self.App.get_service("seacatauth.RoleService")
		tenant_roles = (await role_svc.list(tenant=tenant_id, exclude_global=True))["data"]
		for role in tenant_roles:
			role_id = role["_id"]
			try:
				await role_svc.delete(role_id)
			except KeyError:
				# Role has probably been improperly deleted before; continue
				L.error("Role not found", struct_data={
					"role_id": role_id
				})
				continue

		# Unassign tenant from credentials
		await self.TenantsProvider.delete_tenant_assignments(tenant_id)

		# Delete tenant from provider
		await self.TenantsProvider.delete(tenant_id)

		# Delete sessions that have the tenant in scope
		await session_service.delete_sessions_by_tenant_in_scope(tenant_id)


	def get_provider(self):
		'''
		This method can return None when a 'tenant' feature is not enabled.
		'''
		return self.TenantsProvider


	async def get_tenants(self, credentials_id: str):
		assert (self.is_enabled())  # TODO: Replace this by a L.warning("Tenants are not configured.") & raise RuntimeError()
		# TODO: This has to be cached agressivelly
		result = []
		async for obj in self.TenantsProvider.iterate_assigned(credentials_id):
			result.append(obj['t'])
		return result


	async def set_tenants(self, session, credentials_id: str, tenants: list):
		"""
		Assign `credentials_id` to all tenants listed in `tenants`, unassign it from all tenants that are not listed.
		"""
		assert (self.is_enabled())  # TODO: Replace this by a L.warning("Tenants are not configured.") & raise RuntimeError()
		cred_svc = self.App.get_service("seacatauth.CredentialsService")
		rbac_svc = self.App.get_service("seacatauth.RBACService")

		# Check if credentials exist
		try:
			await cred_svc.detail(credentials_id)
		except KeyError:
			message = "Credentials not found"
			L.error(message, struct_data={"cid": credentials_id})
			return {
				"result": "NOT-FOUND",
				"message": message,
			}

		existing_tenants = set(await self.get_tenants(credentials_id))
		new_tenants = set(tenants)
		tenants_to_assign = new_tenants.difference(existing_tenants)
		tenants_to_unassign = existing_tenants.difference(new_tenants)

		for tenant in tenants_to_assign.union(tenants_to_unassign):
			# Check if tenant exists
			try:
				await self.TenantsProvider.get(tenant)
			except KeyError:
				message = "Tenant not found"
				L.error(message, struct_data={"tenant": tenant})
				return {
					"result": "NOT-FOUND",
					"message": message,
				}
			# Check permission
			if not rbac_svc.has_resource_access(session.Authorization.Authz, tenant, ["seacat:tenant:assign"]):
				message = "Not authorized for tenant un/assignment"
				L.error(message, struct_data={
					"agent_cid": session.Credentials.Id,
					"tenant": tenant
				})
				return {
					"result": "NOT-AUTHORIZED",
					"message": message,
					"error_data": {"tenant": tenant},
				}

		failed_count = 0
		for tenant in tenants_to_assign:
			try:
				await self.assign_tenant(credentials_id, tenant)
			except Exception as e:
				L.error("Failed to assign tenant: {}".format(e), struct_data={
					"cid": credentials_id, "tenant": tenant})
				failed_count += 1

		for tenant in tenants_to_unassign:
			try:
				await self.unassign_tenant(credentials_id, tenant)
			except Exception as e:
				L.error("Failed to unassign tenant: {}".format(e), struct_data={
					"cid": credentials_id, "tenant": tenant})
				failed_count += 1

		L.log(asab.LOG_NOTICE, "Tenants successfully assigned to credentials", struct_data={
			"cid": credentials_id,
			"agent_cid": session.Credentials.Id,
			"assigned_count": len(tenants_to_assign),
			"unassigned_count": len(tenants_to_unassign),
			"failed_count": failed_count,
		})
		return {"result": "OK"}


	async def assign_tenant(
		self, credentials_id: str, tenant: str,
		verify_tenant: bool = True,
		verify_credentials: bool = True
	):
		"""
		Grant tenant access to specified credentials.
		Optionally, verify first that the tenant and the credentials exist.
		"""
		assert (self.is_enabled())

		if verify_tenant:
			try:
				await self.get_tenant(tenant)
			except KeyError:
				raise exceptions.TenantNotFoundError(tenant)

		if verify_credentials:
			credential_service = self.App.get_service("seacatauth.CredentialsService")
			try:
				await credential_service.detail(credentials_id)
			except KeyError:
				raise exceptions.CredentialsNotFoundError(credentials_id)

		try:
			await self.TenantsProvider.assign_tenant(credentials_id, tenant)
		except asab.storage.exceptions.DuplicateError as e:
			if e.KeyValue is not None:
				key, value = e.KeyValue.popitem()
				raise asab.exceptions.Conflict("Tenant already assigned.", key=key, value=value)
			else:
				raise asab.exceptions.Conflict("Tenant already assigned.")

		L.log(asab.LOG_NOTICE, "Tenant assigned to credentials", struct_data={
			"cid": credentials_id,
			"tenant": tenant,
		})


	async def unassign_tenant(self, credentials_id: str, tenant: str):
		"""
		Revoke credentials' access to specified tenant and unassign the tenant's roles.
		"""
		assert (self.is_enabled())

		# Unassign tenant roles
		role_svc = self.App.get_service("seacatauth.RoleService")
		await role_svc.set_roles(
			credentials_id,
			roles=[],
			tenant=tenant
		)

		await self.TenantsProvider.unassign_tenant(credentials_id, tenant)


	def is_enabled(self):
		"""
		Tenants are optional, SeaCat Auth can operate without tenant.
		"""
		return self.TenantsProvider is not None


	async def get_tenants_by_scope(self, scope: list, credential_id: str, has_access_to_all_tenants: bool = False):
		"""
		Returns a set of tenants for given credentials and scope and validates tenant access.

		"tenant:<tenant_name>" in scope requests access to a specific tenant
		"tenant:*" in scope requests access to all the credentials' tenants
		"tenant" in scope ensures at least one tenant is authorized. If no specific tenant is in scope,
			user's last authorized tenant is requested.
		"""
		tenants = set()
		user_tenants = await self.get_tenants(credential_id)
		for resource in scope:
			if not resource.startswith("tenant:"):
				continue
			tenant = resource[len("tenant:"):]
			if tenant == "*":
				# Client is requesting access to all of the user's tenants
				# TODO: Check if the client is allowed to request this
				tenants.update(user_tenants)
			elif tenant in user_tenants:
				tenants.add(tenant)
			elif has_access_to_all_tenants:
				try:
					await self.get_tenant(tenant)
					tenants.add(tenant)
				except KeyError:
					raise exceptions.TenantNotFoundError(tenant)
			else:
				raise exceptions.TenantAccessDeniedError(tenant, credential_id)

		if len(tenants) == 0 and "tenant" in scope:
			last_tenants = [
				tenant
				for tenant in (await self.AuditService.get_last_authorized_tenants(credential_id) or [])
				if tenant in user_tenants
			]
			if last_tenants:
				tenants.add(last_tenants[0])
			elif len(user_tenants) > 0:
				tenants.add(user_tenants[0])
			else:
				raise exceptions.NoTenantsError(credential_id)

		return tenants


	async def has_tenant_assigned(self, credatials_id: str, tenant: str):
		try:
			await self.TenantsProvider.get_assignment(credatials_id, tenant)
		except KeyError:
			return False
		return True
