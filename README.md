# Sovereign Optimizer

Sovereign Optimizer is an air-gapped optimization platform for mathematical
programming, refinery planning, live solver benchmarking, auditability,
constraint compilation, and real-time solver telemetry.

## Demo / No-Login Mode

This build runs without user authentication.

There is:

- No login screen
- No username/PIN authentication
- No session-cookie authentication
- No logout workflow
- No CSRF session token
- No user-management UI
- No PIN management
- No authenticated WebSocket session

The application exposes the platform directly to the user.

For compatibility with older frontend/API code, the backend uses an anonymous
identity:

```text
username: guest
role:     supervisor