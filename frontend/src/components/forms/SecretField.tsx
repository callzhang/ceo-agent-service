import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";

export interface SecretFieldProps {
  id: string;
  label: string;
  value?: string;
  configured?: boolean;
  onChange?: (value: string) => void;
}

export function SecretField({ id, label, value = "", configured = false, onChange }: SecretFieldProps) {
  // A filled input already shows that a credential is stored, so this only
  // marks the field for callers that style or test it.
  void configured;
  const [visible, setVisible] = useState(false);
  return (
    <div className="secret-field">
      <label htmlFor={id}>{label}</label>
      <div className="secret-field-control">
        <input
          id={id}
          name={id}
          type={visible ? "text" : "password"}
          value={value}
          autoComplete="off"
          onChange={(event) => onChange?.(event.target.value)}
        />
        <button
          type="button"
          className="secret-field-toggle"
          aria-label={`${visible ? "隐藏" : "显示"} ${label}`}
          aria-controls={id}
          aria-pressed={visible}
          onClick={() => setVisible((current) => !current)}
        >
          {visible ? <EyeOff aria-hidden="true" size={16} /> : <Eye aria-hidden="true" size={16} />}
        </button>
      </div>
    </div>
  );
}
