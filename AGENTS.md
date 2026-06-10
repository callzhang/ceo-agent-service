# CEO Agent Service Instructions

## Local Service Reload

This project is normally run by launchd as `com.ceo-agent-service.main`. Python code changes are not hot-reloaded by the running service process.

After every commit that changes runtime code, prompt rendering, routing logic, launchd config, or service behavior:

1. Restart the main service:

   ```sh
   launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
   ```

2. Verify the service is running on a new process:

   ```sh
   launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
   ```

3. Check that there is no unresolved `failed` or `processing` backlog before reporting completion.

Do not assume a committed fix is live until the launchd service has been restarted and verified.
