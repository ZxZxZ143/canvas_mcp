"""Application error taxonomy with fixed messages and no arbitrary message input.

Adapters discard unsafe upstream exception text/chains before raising these errors.
Future MCP serialization must project codes, not tracebacks, causes or attributes.
"""

from enum import Enum


class UpstreamReason(str, Enum):
    UNAVAILABLE = "upstream_unavailable"
    TIMEOUT = "upstream_timeout"
    CONNECTION = "upstream_connection_error"
    HTTP = "upstream_http"


class ApplicationError(Exception):
    code = "internal_error"

    def __init__(self, *, retry_exhausted: bool = False) -> None:
        # No caller/upstream message is accepted, retained or formatted.
        if type(retry_exhausted) is not bool:
            raise TypeError()
        self.retry_exhausted = retry_exhausted
        super().__init__(self.code)

    @property
    def diagnostic_code(self) -> str:
        return self.code

    def sanitized(self) -> "ApplicationError":
        """Fresh fixed-field exception, without transport traceback/context."""
        return type(self)(retry_exhausted=self.retry_exhausted)


class AuthenticationError(ApplicationError):
    code = "authentication_error"


class AuthorizationError(ApplicationError):
    code = "authorization_error"


class NetworkPolicyError(ApplicationError):
    code = "network_policy_error"


class NotFoundError(ApplicationError):
    code = "not_found"


class ValidationError(ApplicationError):
    code = "validation_error"


class RateLimitError(ApplicationError):
    code = "rate_limit"


class UpstreamUnavailableError(ApplicationError):
    code = "upstream_unavailable"

    def __init__(
        self,
        *,
        reason: UpstreamReason = UpstreamReason.UNAVAILABLE,
        http_status: int | None = None,
        retry_exhausted: bool = False,
    ) -> None:
        if type(reason) is not UpstreamReason:
            raise TypeError()
        if reason is UpstreamReason.HTTP:
            if type(http_status) is not int or not 500 <= http_status <= 599:
                raise TypeError()
        elif http_status is not None:
            raise TypeError()
        self.reason = reason
        self.http_status = http_status
        super().__init__(retry_exhausted=retry_exhausted)

    @property
    def diagnostic_code(self) -> str:
        if self.reason is UpstreamReason.HTTP:
            return f"upstream_http_{self.http_status}"
        return self.reason.value

    def sanitized(self) -> "UpstreamUnavailableError":
        return UpstreamUnavailableError(
            reason=self.reason, http_status=self.http_status, retry_exhausted=self.retry_exhausted
        )


class DownloadRejectionReason(str, Enum):
    """Fixed categories only: never retain response values or file identities."""

    UNSPECIFIED = "download_rejected"
    METADATA_RESTRICTED = "metadata_restricted"
    UNSUPPORTED_EXTENSION = "unsupported_file_extension"
    MIME_MISMATCH = "mime_evidence_mismatch"
    INVALID_TEXT = "invalid_text_encoding"
    UNSAFE_PREFIX = "unsafe_content_prefix"
    PREFIX_MISMATCH = "prefix_mismatch"
    ANONYMOUS_UNAUTHORIZED = "anonymous_download_unauthorized"
    UNSUPPORTED_ENCODING = "unsupported_content_encoding"
    INVALID_LENGTH = "invalid_content_length"
    LENGTH_MISMATCH = "content_length_mismatch"
    CREDENTIAL_REFLECTION = "credential_reflection"
    PROTOCOL_ERROR = "transfer_protocol_error"


class MimeMismatchReason(str, Enum):
    CANVAS_MIME_VS_EXTENSION = "canvas_mime_vs_extension"
    HTTP_MIME_VS_EXTENSION = "http_mime_vs_extension"
    HTTP_MIME_MISSING = "http_mime_missing"


class DownloadRejectedError(ApplicationError):
    code = "download_rejected"

    def __init__(
        self,
        *,
        reason: DownloadRejectionReason = DownloadRejectionReason.UNSPECIFIED,
        mime_reason: MimeMismatchReason | None = None,
        retry_exhausted: bool = False,
    ) -> None:
        if type(reason) is not DownloadRejectionReason:
            raise TypeError()
        if mime_reason is not None and (
            type(mime_reason) is not MimeMismatchReason
            or reason is not DownloadRejectionReason.MIME_MISMATCH
        ):
            raise TypeError()
        self.reason = reason
        self.mime_reason = mime_reason
        super().__init__(retry_exhausted=retry_exhausted)

    @property
    def diagnostic_code(self) -> str:
        # Subclasses retain their existing distinct public/safety codes.
        if self.code != "download_rejected":
            return self.code
        return self.reason.value + ("/" + self.mime_reason.value if self.mime_reason else "")

    def sanitized(self) -> "DownloadRejectedError":
        return type(self)(
            reason=self.reason, mime_reason=self.mime_reason, retry_exhausted=self.retry_exhausted
        )


class FileTooLargeError(DownloadRejectedError):
    code = "file_too_large"


class UnsafeRedirectError(DownloadRejectedError):
    code = "unsafe_redirect"


class DownloadPermissionUnavailableError(ApplicationError):
    code = "download_permission_unavailable"


class PublicUrlOriginUnapprovedError(ApplicationError):
    code = "public_url_origin_unapproved"


class StorageError(ApplicationError):
    code = "storage_error"


class DownloadTimeoutError(ApplicationError):
    code = "download_timeout"


class ConfigurationError(ApplicationError):
    code = "configuration_error"


class MalformedUpstreamError(ApplicationError):
    code = "malformed_upstream"


class UnsupportedCapabilityError(ApplicationError):
    code = "unsupported_capability"


class BudgetExceededError(ApplicationError):
    code = "budget_exceeded"


class RequestBudgetExceededError(BudgetExceededError):
    """Shared deadline/attempt/page ledger exhausted, not a content/safety limit."""

    code = "request_budget_exceeded"


class ArtifactUnavailableError(ApplicationError):
    code = "artifact_unavailable"


class UnsupportedFileFormatError(ApplicationError):
    code = "unsupported_file_format"


class FileContentUnavailableError(ApplicationError):
    code = "file_content_unavailable"


class FileContentTimeoutError(ApplicationError):
    code = "file_content_timeout"


class FileContentTooLargeError(ApplicationError):
    code = "file_content_too_large"


class FileParseError(ApplicationError):
    code = "file_parse_error"
