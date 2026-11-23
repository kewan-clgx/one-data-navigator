import type { Metadata } from "next";

import SiteFrame from "../components/SiteFrame";
import { appHost, supportEmail } from "@/lib/site";

export const metadata: Metadata = {
  title: "Privacy policy — QueryMuse",
  description: "How QueryMuse collects, uses, stores and protects information, including Google user data.",
};

const EFFECTIVE = "November 19, 2026";

export default function Privacy() {
  const support = supportEmail();
  const mail = <a href={`mailto:${support}`}>{support}</a>;
  return (
    <SiteFrame>
      <article className="odn-site-doc">
        <h1>Privacy policy</h1>
        <p className="odn-site-muted">Effective {EFFECTIVE}</p>

        <p>
          This policy explains how QueryMuse (&ldquo;QueryMuse&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;) handles
          information when you use the QueryMuse conversational data analytics platform and website at {appHost()}. QueryMuse is provided to organizations (our customers); if you use it through
          your employer, your organization controls the data it connects to QueryMuse and the accounts it creates.
        </p>

        <h2>Information we collect</h2>
        <ul>
          <li>
            <strong>Account information:</strong> your name, email address, the organization you belong to, your
            role, and sign-in times. If you sign in with Google, we receive your name, email address and an account
            identifier from Google (the <code>openid</code>, <code>email</code> and <code>profile</code> scopes). If
            you sign in with a password, the password is handled by Google Identity Platform; we never store it.
          </li>
          <li>
            <strong>Content you provide:</strong> questions you ask, conversation history, SQL you run, query
            results, charts, and files you upload.
          </li>
          <li>
            <strong>Connection settings:</strong> details of data sources and AI-model keys that you or your
            organization add. Passwords, private keys and API keys are encrypted at rest and used only to perform
            the actions you request.
          </li>
          <li>
            <strong>Security and usage records:</strong> an audit log of queries, sign-in events (including failed
            sign-ins, without the password), and usage counts such as AI tokens used.
          </li>
        </ul>

        <h2>How we use information</h2>
        <ul>
          <li>To sign you in, confirm which organization you belong to, and apply your permissions.</li>
          <li>
            To answer your questions: running read-only queries against the data sources your organization
            connected, and showing results, charts and explanations.
          </li>
          <li>To keep the service secure, enforce usage limits, investigate abuse and fix problems.</li>
          <li>To send service emails such as invitations and password resets, and to answer support requests.</li>
        </ul>
        <p>We do not sell personal information, use it for advertising, or use your content to train AI models.</p>

        <h2>Google user data</h2>
        <p>
          When you sign in with Google, we use your Google account&rsquo;s name, email address and identifier only to
          authenticate you and show who is signed in. We do not request access to your Gmail, Drive, Calendar or other
          Google data. QueryMuse&rsquo;s use and transfer of information received from Google APIs adheres to the{" "}
          <a href="https://developers.google.com/terms/api-services-user-data-policy">
            Google API Services User Data Policy
          </a>
          , including the Limited Use requirements. We do not transfer Google user data to others except as needed to
          provide the service, for security, or as required by law, and never for advertising.
        </p>

        <h2>AI processing</h2>
        <p>
          To answer a question, QueryMuse sends the question, relevant table and column descriptions, and samples of
          query results or uploaded files to an AI model. By default this is Google&rsquo;s Gemini API; if you add
          your own AI-provider key, requests go to that provider under your agreement with them.
        </p>

        <h2>Service providers</h2>
        <p>We share information only with providers that help us run QueryMuse, under contracts that limit their use:</p>
        <ul>
          <li>Google Cloud (hosting and storage in the United States, sign-in via Identity Platform, Gemini AI models).</li>
          <li>Resend (sending invitation and password-reset emails).</li>
          <li>Microsoft (our support mailbox).</li>
          <li>AI providers you choose by adding your own key.</li>
        </ul>
        <p>We may also disclose information if required by law or to protect the rights and safety of users and the service.</p>

        <h2>Cookies</h2>
        <p>
          We use only essential cookies: an encrypted session cookie that keeps you signed in, a short-lived cookie
          that protects the sign-in flow, and one that remembers your organization name on the sign-in form. We do
          not use advertising or analytics cookies.
        </p>

        <h2>Storage, security and retention</h2>
        <p>
          Data is stored on Google Cloud in the United States, encrypted in transit (HTTPS) and at rest. Each
          organization&rsquo;s data is kept separate by row-level security in our database and per-organization
          storage paths. We keep account information and content while the account or organization uses QueryMuse.
          You can delete conversations in the app; to delete your account or your organization&rsquo;s data, contact
          your organization&rsquo;s administrator or write to {mail}. We delete it within 30 days of a verified
          request, except records we must keep for legal or security reasons.
        </p>

        <h2>Your choices and rights</h2>
        <p>
          Depending on where you live, you may have the right to access, correct, delete or export your personal
          information, or to object to its processing. To make a request, write to {mail}. If you use QueryMuse
          through your organization, we may refer your request to them. You can revoke QueryMuse&rsquo;s access to
          your Google account at any time at{" "}
          <a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>.
        </p>

        <h2>Children</h2>
        <p>QueryMuse is a business service and is not directed to children under 16.</p>

        <h2>Changes</h2>
        <p>
          We will post changes to this page and update the effective date; significant changes will be announced in
          the app or by email.
        </p>

        <h2>Contact</h2>
        <p>Questions about this policy or your data: {mail}.</p>
      </article>
    </SiteFrame>
  );
}
