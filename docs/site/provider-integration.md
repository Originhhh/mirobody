# Adding a device provider

A provider connects a device service to Mirobody. Before writing one, check
whether an existing integration or file import already handles the source.

## Implementation contract

Keep decoding separate from network access. The decoder converts a vendor
payload into supported metrics; the provider handles credentials, requests,
callbacks and synchronization.

The provider contract includes:

- `format_data(self, fmt_input: FormatDataInput) -> StandardPulseData`
- `_validate_credentials(self, credentials: dict)`

Read settings through the configuration system. An optional integration with
missing credentials should not register as an available provider.
All observation writes must use the shared collection path; catalogue units
remain the authority for decoded values.

## Registration

Add the provider directory to `PROVIDER_DIRS`, retaining the default:

```yaml
PROVIDER_DIRS:
  - mirobody/collect/providers
  - my_providers
```

An installed package can register through the `mirobody.providers` entry point.
Use synthetic payloads to check unit conversion, timestamp handling, missing
fields, repeated deliveries and authentication failures before connecting real
accounts. Logs must not contain readings or credentials.

The [provider guide](../provider-guide.md) includes complete class examples,
OAuth and webhook handling, decoder samples and registration details.
For existing integrations, see [Device Providers](../provider-setup.md).
