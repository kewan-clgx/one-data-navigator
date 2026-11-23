import type { Metadata } from "next";

import SiteFrame from "../components/SiteFrame";
import { appHost, supportEmail } from "@/lib/site";

export const metadata: Metadata = {
  title: "Terms of service — QueryMuse",
  description: "The terms for using QueryMuse, the conversational data analytics platform.",
};

const EFFECTIVE = "23 November 2026";

export default function Terms() {
  const support = supportEmail();
  const mail = <a href={`mailto:${support}`}>{support}</a>;
  return (
    <SiteFrame>
      <article className="odn-site-doc">
        <h1>Terms of service</h1>
        <p className="odn-site-muted">Effective {EFFECTIVE}</p>

        <p>
          These terms apply to your use of QueryMuse (&ldquo;QueryMuse&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;),
          the conversational data analytics platform and website at {appHost()}. If you use QueryMuse through your
          organization, your organization&rsquo;s agreement with us also applies and takes precedence where they
          differ. By signing in, you agree to these terms.
        </p>

        <h2>Your account</h2>
        <p>
          Keep your sign-in details secure and use QueryMuse only with accounts you are authorized to use. Your
          organization&rsquo;s administrators can manage, suspend or remove accounts in their organization.
        </p>

        <h2>Your data</h2>
        <p>
          You and your organization keep all rights to the data you connect, upload or create in QueryMuse. We use it
          only to provide the service, as described in our <a href="/privacy">privacy policy</a>. You are responsible
          for having the right to connect and query that data, and for what you choose to share (for example through
          share links or the Insight Wall).
        </p>

        <h2>Acceptable use</h2>
        <p>
          Don&rsquo;t use QueryMuse to break the law, to access data you aren&rsquo;t allowed to access, to probe or
          disrupt the service or other organizations, or to send harmful content. We may suspend access that puts the
          service or other customers at risk.
        </p>

        <h2>AI-generated answers</h2>
        <p>
          QueryMuse uses AI models to interpret questions and write queries and explanations. Answers can be
          incomplete or wrong: check important results (the SQL and source tables are shown with each answer) before
          relying on them.
        </p>

        <h2>Availability and changes</h2>
        <p>
          QueryMuse is in beta. We work to keep it available and secure but provide it &ldquo;as is&rdquo;, without
          warranties to the extent the law allows, and we may change or discontinue features. We&rsquo;ll give notice
          of material changes to these terms in the app or by email.
        </p>

        <h2>Liability</h2>
        <p>
          To the extent the law allows, we are not liable for indirect or consequential losses, and our total
          liability is limited to the amounts your organization paid for QueryMuse in the 12 months before the claim.
        </p>

        <h2>Contact</h2>
        <p>Questions about these terms: {mail}.</p>
      </article>
    </SiteFrame>
  );
}
