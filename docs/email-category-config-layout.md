# Email category configuration layout

The email configuration panel uses two columns: a vertical category navigation
and the selected category's core definition, include/exclude scenarios,
auto-process threshold and enabled state. The navigation uses pressed buttons associated with the editor
region and retains the application's existing visual tokens and controls.

On narrow screens the navigation shrinks to 80px and the editor fields stack.
Save/load disabled states and action restrictions remain unchanged. Switching
categories is a local selection and never submits configuration automatically.

Validation covers every navigation entry, selected-state highlighting,
the matching editor and no implicit save.
Existing configuration validation and explicit-save tests remain in place.
