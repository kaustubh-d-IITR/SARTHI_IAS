import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getHealth } from "../lib/api";
import type { HealthResponse } from "../types";

export type BackendPhase = "checking" | "online" | "offline";

type BackendValue = {
  phase: BackendPhase;
  health: HealthResponse | null;
};

const BackendContext = createContext<BackendValue>({ phase: "checking", health: null });

export function BackendProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<BackendPhase>("checking");
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => {
      getHealth()
        .then((body) => {
          if (!active) return;
          setHealth(body);
          setPhase(body.status === "ok" && body.upload_directory_writable ? "online" : "offline");
        })
        .catch(() => {
          if (!active) return;
          setHealth(null);
          setPhase("offline");
        });
    };
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return <BackendContext.Provider value={{ phase, health }}>{children}</BackendContext.Provider>;
}

export function useBackend(): BackendValue {
  return useContext(BackendContext);
}
