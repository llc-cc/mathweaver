import {
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
} from "react-router";

import React, { useState } from "react";

import type { Route } from "./+types/root";
import "./Main.css";
import "./Entry.css";
import "./Operator.css";
import "./Morpheme.css";

import { Settings } from "./Settings";
import { JobsProvider } from "./context/jobs";

// Google Fonts is render-blocking via a normal <link rel="stylesheet"> and is
// frequently slow/unreachable on some networks (e.g. mainland China), which can
// freeze the page for ~30s while the request times out. Load it NON-blocking:
// the page renders immediately with the CSS system-font fallbacks, and the web
// fonts swap in once (if) they arrive. See the manual <head> links below.
const FONT_HREF =
  "https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600;8..60,700&family=Noto+Serif+SC:wght@500;600;700&display=swap";

export const links: Route.LinksFunction = () => [
  { rel: "icon", href: "/mathweaver-icon.png", type: "image/png" },
  { rel: "preconnect", href: "https://fonts.googleapis.com" },
  {
    rel: "preconnect",
    href: "https://fonts.gstatic.com",
    crossOrigin: "anonymous",
  },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const [language, setLanguage] = useState("EN");
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
        {/* Non-blocking web-font load: preload as style, then promote to a
            stylesheet on load so it never blocks first render. */}
        <link
          rel="preload"
          as="style"
          href={FONT_HREF}
          onLoad={(e) => {
            const l = e.currentTarget as HTMLLinkElement;
            l.onload = null;
            l.rel = "stylesheet";
          }}
        />
        <noscript>
          <link rel="stylesheet" href={FONT_HREF} />
        </noscript>
      </head>
      <body>
        <Settings.Provider value={{
            theme: "dark",
            language: language,
            setLanguage: setLanguage
        }}>
          <JobsProvider>
            {children}
          </JobsProvider>
        </Settings.Provider>
        <ScrollRestoration />
        <Scripts />
       
      </body>
    </html>
  );
}

export default function App() {
  return <Outlet />;
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  let message = "Oops!";
  let details = "An unexpected error occurred.";
  let stack: string | undefined;

  if (isRouteErrorResponse(error)) {
    message = error.status === 404 ? "404" : "Error";
    details =
      error.status === 404
        ? "The requested page could not be found."
        : error.statusText || details;
  } else if (import.meta.env.DEV && error && error instanceof Error) {
    details = error.message;
    stack = error.stack;
  }

  return (
    <main className="pt-16 p-4 container mx-auto">
      <h1>{message}</h1>
      <p>{details}</p>
      {stack && (
        <pre className="w-full p-4 overflow-x-auto">
          <code>{stack}</code>
        </pre>
      )}
    </main>
  );
}
