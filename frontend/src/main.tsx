import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ConsoleRouter } from "./app/router";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("Workbench root element is missing");

createRoot(root).render(
  <StrictMode>
    <ConsoleRouter />
  </StrictMode>,
);
