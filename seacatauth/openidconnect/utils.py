class AuthErrorResponseCode:
	# OAuth2.0 error response codes defined in RFC6749
	# https://www.ietf.org/rfc/rfc6749.txt  section 4.1.2.1.
	InvalidRequest = "invalid_request"
	UnauthorizedClient = "unauthorized_client"
	AccessDenied = "access_denied"
	UnsupportedResponseType = "unsupported_response_type"
	InvalidScope = "invalid_scope"
	ServerError = "server_error"
	TemporarilyUnavailable = "temporarily_unavailable"

	# OIDC error response codes defined in OIDC Core 1.0
	# https://openid.net/specs/openid-connect-core-1_0.html#AuthError
	InteractionRequired = "interaction_required"
	LoginRequired = "login_required"
	AccountSelectionRequired = "account_selection_required"
	ConsentRequired = "consent_required"
	InvalidRequestUri = "invalid_request_uri"
	InvalidRequestObject = "invalid_request_object"
	RequestNotSupported = "request_not_supported"
	RequestUriNotSupported = "request_uri_not_supported"
	RegistrationNotSupported = "registration_not_supported"


class TokenRequestErrorResponseCode:
	# OAuth2.0 token request error response codes defined in RFC6749
	# https://datatracker.ietf.org/doc/html/rfc6749#section-5.2
	InvalidRequest = "invalid_request"
	InvalidClient = "invalid_client"
	InvalidGrant = "invalid_grant"
	UnauthorizedClient = "unauthorized_client"
	UnsupportedGrantType = "unsupported_grant_type"
	InvalidScope = "invalid_scope"


class InvalidGrantError(Exception):
	def __init__(self, *args, client_id=None):
		self.ClientId = client_id
		super().__init__("Invalid grant.", *args)
