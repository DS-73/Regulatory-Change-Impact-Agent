# Regulatory Change Impact Agent

**Category:** AI for Regulatory Intelligence, Compliance & BFSI
**Applicable Regions:** Saudi Arabia (KSA), United Arab Emirates (UAE), India and selected African countries
**Regulatory Focus:** Artificial Intelligence, Banking, Financial Services and Insurance (BFSI)

## Problem Statement

Financial institutions operate in a constantly evolving regulatory environment. Governments, central banks, financial regulators and other supervisory authorities regularly publish new regulations, circulars, amendments, guidelines and compliance requirements.

These changes may introduce new obligations, modify existing regulations, extend compliance deadlines, withdraw earlier instructions or introduce requirements that affect how financial institutions operate.

Today, compliance teams often monitor regulatory websites manually, download circulars, compare them with earlier regulations, interpret the changes and identify which internal policies, processes, systems and controls need to be updated. This process is time-consuming, particularly for organisations operating across multiple countries.

The challenge is to build an AI-powered Regulatory Change Impact Agent that continuously monitors regulatory publications across KSA, UAE, India and selected African countries, understands how new regulations relate to existing ones, and identifies the changes organisations need to make to remain compliant.

The solution should go beyond summarising regulatory announcements. It must establish relationships between current and historical regulations, identify new or modified obligations, assess their impact on internal policies and controls, and recommend the necessary changes.

For example, if a regulator introduces a new requirement governing the use of AI in credit decisioning, the system should identify the previous regulations governing that activity, determine what has changed, identify affected internal credit policies and AI governance controls, and recommend specific modifications. The application must also support human review, approvals, accountability and traceability so that compliance teams can confidently act on its recommendations.

## Level 1: Monitor, Discover and Understand Regulatory Changes

**Suggested time:** 0 - 3 hours

**Objective:** Build a working regulatory intelligence application capable of discovering regulatory publications, extracting their contents and identifying changes against previously published regulations.

### Required Capabilities

#### 1. Regulatory Source Monitoring

Build a monitoring mechanism that can retrieve regulatory publications from official government and regulatory authority websites across the defined regions.

The application should support configurable regulatory sources. Examples include SAMA and other relevant Saudi authorities, the Central Bank of the UAE, the Reserve Bank of India, and selected African central banks or financial regulators. Participants must demonstrate monitoring of at least four configured sources, representing the four geographic regions. The system should:

- Retrieve newly published regulations, circulars, amendments, consultation papers and guidelines.
- Identify the issuing authority, jurisdiction, publication date, document title, reference number and effective date, wherever available.
- Distinguish newly published documents from documents already processed.
- Allow users to configure monitoring frequency and manually trigger a monitoring cycle.
- Maintain a searchable repository of all discovered regulatory publications.
- Support manual upload of PDF, DOCX or text-based circulars when automatic retrieval is unavailable.

#### 2. Regulatory Document Understanding

Once a document is retrieved or uploaded, the system should analyse its contents and extract relevant regulatory information. The application should identify:

- Regulatory obligations and mandatory requirements.
- Applicability to AI, banking, lending, insurance, payments or other BFSI activities.
- Compliance deadlines and effective dates.
- References to earlier regulations or circulars.
- Clauses that introduce, amend, replace or withdraw existing requirements.
- Entities, departments or regulated activities to which the requirements apply.

The extracted information should be presented in a structured format rather than only as an AI-generated summary. Each extracted obligation must include a reference to the relevant clause or section of the source document.
