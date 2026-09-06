# Email category configuration layout

The email configuration panel uses two columns: a vertical category navigation
and the selected category's description, threshold, version, enabled state and
action controls. The navigation uses pressed buttons associated with the editor
region and retains the application's existing visual tokens and controls.

On narrow screens the navigation shrinks to 80px and the editor fields stack.
Save/load disabled states and action restrictions remain unchanged. Switching
categories is a local selection and never submits configuration automatically.

Validation covers all eight navigation entries, selected-state highlighting,
the matching editor, subscription action availability and no implicit save.
Existing configuration validation and explicit-save tests remain in place.
