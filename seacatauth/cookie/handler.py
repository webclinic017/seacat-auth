import logging
import re

import aiohttp
import aiohttp.web

from ..generic import add_to_header
from .utils import set_cookie, delete_cookie

#

L = logging.getLogger(__name__)

#


class CookieHandler(object):


	def __init__(self, app, cookie_svc, session_svc, credentials_svc):
		self.App = app
		self.CookieService = cookie_svc
		self.SessionService = session_svc
		self.CredentialsService = credentials_svc
		self.RBACService = app.get_service("seacatauth.RBACService")

		self.CookiePattern = re.compile(
			"(^{cookie}=[^;]*; ?|; ?{cookie}=[^;]*)".format(cookie=self.CookieService.CookieName)
		)

		web_app = app.WebContainer.WebApp
		web_app.router.add_post('/cookie/nginx', self.nginx)
		web_app.router.add_get('/cookie/entry/{domain_id}', self.cookie_request)

		# Public endpoints
		web_app_public = app.PublicWebContainer.WebApp
		web_app_public.router.add_post('/cookie/nginx', self.nginx)
		web_app_public.router.add_get('/cookie/entry/{domain_id}', self.cookie_request)


	async def nginx(self, request):
		"""
		Validate the session cookie and exchange it for a Bearer token.
		Add requested user info to headers.

		Example Nginx setup:
		```nginx
		# Protected location
		location /my-app {
			auth_request /_cookie_introspect;
			auth_request_set      $authorization $upstream_http_authorization;
			proxy_set_header      Authorization $authorization;
			proxy_pass            http://my-app:8080
		}

		# Introspection endpoint
		location = /_cookie_introspect {
			internal;
			proxy_method          POST;
			proxy_set_header      X-Request-URI "$request_uri";
			proxy_set_body        "$http_authorization";
			proxy_pass            http://seacat-auth-svc:8081/cookie/nginx?add=credentials;
		}
		```
		"""

		attributes_to_add = request.query.getall("add", [])
		attributes_to_verify = request.query.getall("verify", [])

		# Authorize request
		# Use custom authorization since it must use cookie, not the authn header
		session = await self.CookieService.get_session_by_sci(request)
		if session is None:
			response = aiohttp.web.HTTPUnauthorized()
			delete_cookie(self.App, response)
			return response

		# Check tenant+resource access
		requested_tenant = None
		requested_resources = set()
		if len(attributes_to_verify) > 0:
			if "resources" in attributes_to_verify:
				requested_resources.update(request.headers.get("X-Resources").split(" "))

			if "tenant" in attributes_to_verify:
				requested_tenant = request.headers.get("X-Tenant")
				requested_resources.add("tenant:access")

			if self.RBACService.has_resource_access(session.Authz, requested_tenant, requested_resources) != "OK":
				L.warning("Credentials not authorized for tenant or resource.", struct_data={
					"cid": session.CredentialsId,
					"tenant": requested_tenant,
					"resources": " ".join(requested_resources),
				})
				return aiohttp.web.HTTPForbidden()

		# Extend session expiration
		await self.SessionService.touch(session)

		# Add Bearer token to Authorization header
		headers = {
			aiohttp.hdrs.AUTHORIZATION: "Bearer {}".format(session.OAuth2['access_token'])
		}

		# Delete SeaCat cookie from Cookie header unless "keepcookie" param is passed in query
		keep_cookie = request.query.get("keepcookie", None)
		cookie_string = request.headers.get(aiohttp.hdrs.COOKIE)

		if keep_cookie is None:
			cookie_string = self.CookiePattern.sub("", cookie_string)

		headers[aiohttp.hdrs.COOKIE] = cookie_string

		# Add requested X-Headers
		headers = await add_to_header(
			headers,
			attributes_to_add,
			session,
			self.CredentialsService,
			requested_tenant=requested_tenant
		)

		return aiohttp.web.HTTPOk(headers=headers)


	async def cookie_request(self, request):
		"""
		Exchange authorization code for cookie and redirect afterwards.
		"""
		grant_type = request.query.get("grant_type")

		if grant_type != "authorization_code":
			L.warning("Grant type not supported", struct_data={"grant_type": grant_type})
			return aiohttp.web.HTTPBadRequest()

		# Use the code to get session ID
		code = request.query.get("code")
		session = await self.CookieService.get_session_by_authorization_code(code)
		if session is None:
			return aiohttp.web.HTTPBadRequest()

		# Construct the response
		# TODO: Dynamic redirect (instead of static URL from config)
		domain_id = request.match_info["domain_id"]
		if domain_id not in self.CookieService.ApplicationCookies:
			L.error("Invalid domain ID", struct_data={"domain_id": domain_id})

		redirect_uri = self.CookieService.ApplicationCookies[domain_id]["redirect_uri"]

		response = aiohttp.web.HTTPFound(
			redirect_uri,
			headers={
				"Refresh": '0;url=' + redirect_uri,
				"Location": redirect_uri,
			},
			content_type="text/html",
			text="<!doctype html>\n<html lang=\"en\">\n<head></head><body>...</body>\n</html>\n"
		)

		# TODO: Verify that the request came from the correct domain
		try:
			set_cookie(self.App, response, session, domain_id)
		except KeyError:
			L.error("Failed to set cookie", struct_data={"sid": session.SessionId, "domain_id": domain_id})
			return

		return response
