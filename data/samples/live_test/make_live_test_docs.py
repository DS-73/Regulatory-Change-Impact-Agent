"""Builds the four live-test documents (run: python data/samples/live_test/make_live_test_docs.py).

All are illustrative test documents for a fictional bank, not official publications.

Internal policies  -> upload under Policies (Document type: Policy)
  1. POL-CX_complaints_customer_communication_policy.docx   owner Compliance
  2. POL-OB_open_banking_api_security_policy.docx           owner Ops, area payments
Regulatory documents -> upload under Sources → Add a document
  3. CBUAE_notice_consumer_protection_open_finance_2026.pdf  one of the 4 configured regulators
  4. CBK_guidance_cybersecurity_psp_2026.pdf                 Central Bank of Kenya: NOT one of the 4
"""
from pathlib import Path

import docx
import pymupdf

OUT = Path(__file__).parent
NOTE = "[Illustrative test document for the RegWatch live demo; not an official publication.]"


def make_docx(path: Path, lines: list[str]) -> None:
    d = docx.Document()
    for i, line in enumerate(lines):
        if i == 0:
            d.add_heading(line, level=1)
        else:
            d.add_paragraph(line)
    d.save(path)


def make_pdf(path: Path, lines: list[str]) -> None:
    doc = pymupdf.open()
    page, y = doc.new_page(), 60
    for line in lines:
        for chunk in [line[i:i + 105] for i in range(0, max(len(line), 1), 105)]:
            page.insert_text((50, y), chunk, fontsize=10)
            y += 15
            if y > 790:
                page, y = doc.new_page(), 60
        y += 3
    path.write_bytes(doc.tobytes())


COMPLAINTS_POLICY = [
    "Complaints Handling and Customer Communication Policy",
    "Document: POL-CX-08 · Owner: Compliance · Approved September 2026",
    NOTE,
    "1. Purpose",
    "This policy sets how the Bank receives, handles and resolves customer complaints and how it communicates "
    "decisions to customers.",
    "2. Complaint handling",
    "2.1 Every complaint is acknowledged to the customer within five working days of receipt.",
    "2.2 Complaints are investigated and resolved within 30 working days, and the outcome is sent to the customer in writing.",
    "2.3 No fee is charged to customers for making a complaint.",
    "3. Customer communication",
    "3.1 When a credit or account application is declined, the customer is told the main reasons in writing.",
    "3.2 Customer communications are issued in Arabic and English.",
    "4. Escalation",
    "4.1 Customers are informed of their right to escalate an unresolved complaint to the regulator.",
    "5. Records",
    "5.1 Complaint records and correspondence are retained for five years.",
]

OPEN_BANKING_POLICY = [
    "Open Banking and API Security Policy",
    "Document: POL-OB-09 · Owner: Ops · Approved October 2026",
    NOTE,
    "1. Scope",
    "This policy covers open banking and open finance services, payment initiation and account information APIs "
    "offered to third-party providers.",
    "2. Third-party providers",
    "2.1 Third-party providers are onboarded and approved by Ops before they receive production API credentials.",
    "3. Customer consent",
    "3.1 Explicit customer consent is obtained before any account data is shared with a third-party provider; consent is "
    "valid for a maximum of 12 months.",
    "3.2 Customers can revoke consent at any time through the mobile app.",
    "4. Security",
    "4.1 Strong customer authentication with two factors is required for every payment initiation.",
    "4.2 API access logs and security logs are retained for one year.",
    "4.3 The API gateway undergoes an independent penetration test once a year.",
    "5. Incidents",
    "5.1 Security incidents affecting customer-facing APIs are escalated to the CISO within 24 hours and reported to the "
    "regulator within 72 hours.",
]

CBUAE_NOTICE = [
    "CENTRAL BANK OF THE UNITED ARAB EMIRATES",
    "Notice on Consumer Protection in Digital Banking and Open Finance",
    "Notice No.: CBUAE/ILLUS/N/2026/27",
    "Date: 30 October 2026",
    NOTE,
    "To: All Licensed Financial Institutions",
    "1. Scope",
    "1.1 This Notice applies to all licensed financial institutions that provide digital banking or open finance services "
    "to consumers in the UAE.",
    "2. Complaints",
    "2.1 Licensed financial institutions shall acknowledge every consumer complaint within two working days of receipt.",
    "2.2 Licensed financial institutions shall resolve complaints within 15 working days and inform the consumer of the "
    "outcome in writing.",
    "2.3 Licensed financial institutions shall not charge any fee for submitting a complaint.",
    "3. Automated decisions",
    "3.1 Licensed financial institutions shall inform consumers when a decision that materially affects them is made solely "
    "by an AI model, and shall give them the right to request a human review of that decision.",
    "4. Open finance consent",
    "4.1 Licensed financial institutions shall obtain explicit consumer consent before sharing account data with an open "
    "finance participant; consent shall not exceed 12 months.",
    "4.2 Consumers shall be able to revoke consent at any time.",
    "5. Security",
    "5.1 Licensed financial institutions shall apply strong customer authentication with two factors to payment initiation.",
    "5.2 Licensed financial institutions shall report material cyber incidents affecting open finance APIs to the Central "
    "Bank within 24 hours.",
    "6. Implementation",
    "6.1 Licensed financial institutions shall comply with this Notice by 1 March 2027.",
]

CBK_GUIDANCE = [
    "CENTRAL BANK OF KENYA",
    "Guidance Note on Cybersecurity for Payment Service Providers",
    "Ref: CBK/ILLUS/PSP/2026/04",
    "Date: 15 October 2026",
    NOTE,
    "To: All Payment Service Providers and Banks offering payment services",
    "1. Purpose",
    "This Guidance Note sets minimum cybersecurity requirements for payment service providers regulated by the Central "
    "Bank of Kenya.",
    "2. Governance",
    "2.1 Payment service providers shall maintain a cybersecurity policy approved by the board of directors and reviewed "
    "at least annually.",
    "3. Controls",
    "3.1 Payment service providers shall apply multi-factor authentication to all customer-facing payment channels.",
    "3.2 Payment service providers shall commission independent penetration testing of internet-facing payment systems at "
    "least twice a year.",
    "3.3 Payment service providers shall retain security and access logs for at least two years.",
    "4. Incident reporting",
    "4.1 Payment service providers shall report any cyber incident affecting payment services to the Central Bank of Kenya "
    "within 24 hours of detection.",
    "5. Compliance date",
    "5.1 Payment service providers shall comply with this Guidance Note by 30 June 2027.",
]

if __name__ == "__main__":
    make_docx(OUT / "POL-CX_complaints_customer_communication_policy.docx", COMPLAINTS_POLICY)
    make_docx(OUT / "POL-OB_open_banking_api_security_policy.docx", OPEN_BANKING_POLICY)
    make_pdf(OUT / "CBUAE_notice_consumer_protection_open_finance_2026.pdf", CBUAE_NOTICE)
    make_pdf(OUT / "CBK_guidance_cybersecurity_psp_2026.pdf", CBK_GUIDANCE)
    for p in sorted(OUT.glob("*.*")):
        if p.suffix in (".docx", ".pdf"):
            print(f"{p.name:<58} {p.stat().st_size:>7} bytes")
