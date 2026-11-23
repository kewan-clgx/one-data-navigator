"use client";

import { createContext, useContext } from "react";

/** Deployment addresses for client components (from lib/site.ts on the server). */
export type SiteInfo = { wallAddress: string; supportEmail: string };

export const SiteContext = createContext<SiteInfo>({ wallAddress: "", supportEmail: "" });

export const useSite = () => useContext(SiteContext);
