# Research 14: e-Arsiv Fatura options and technical requirements

Researched 2026-11-09. Facts only, no design decisions. Extends [research 04 section 6](04-turkey-commerce-rules.md) (legal obligation) with the issuing options and technical requirements. Not repeated here: who is obliged and from when (500,000 TL limit, 3,000 TL / all-amounts rule from 1 Jan 2026 or 1 Jan 2027 for basit usul and isletme hesabi).

Reading convention: "official text read" means I downloaded the GIB PDF and extracted its text locally. Items marked NOT CONFIRMED were not found in a primary source. Items marked **[ACCOUNTANT]** need sign-off. Seller profile as in map.md: small Mersin women's clothing shop, own Django site, bank transfer and COD only, tax regime unknown.

Sources used (all GIB unless stated):
- [S1] VUK Genel Teblig 509 consolidated, 2026 (official text read): [PDF](https://cdn.gib.gov.tr/api/gibportal-file/file/getFile?objectKey=MEVZUAT_TEBLIGLER%2FUNIVERSAL%2F2026%2FMEVZUAT_TEBLIGLER_2026_VukTeb509_Guncel.pdf)
- [S2] Elektronik Arsiv Kilavuzu v1.20, November 2026 (official text read): [PDF](https://ebelge.gib.gov.tr/dosyalar/kilavuzlar/e-Arsiv_Teknik_Kilavuzu_V.1.20.pdf)
- [S3] GIB announcement, 18/12/2019, "1.1.2020 e-Arsiv Fatura" (official text read): [PDF](https://ebelge.gib.gov.tr/dosyalar/e-Arsiv_Fatura_Duyurusu.pdf)
- [S4] GIB Portal application document (official text read): [PDF](https://ebelge.gib.gov.tr/dosyalar/kilavuzlar/e-Arsiv_Portal_Basvuru_Dokumani.pdf)
- [S5] e-Arsiv Uygulamalari Iptal/Ihtar/Itiraz Bildirim Kilavuzu v1.1, Jan 2025 (official text read): [PDF](https://ebelge.gib.gov.tr/dosyalar/kilavuzlar/e-Arsiv_Uygulamalari_Iptal_Ihtar_Itiraz_Bildirim_Kilavuzu_V.1.1.pdf)
- [S6] GIB e-Belge "Yararlanma Yontemleri" page (page text, read via local extraction): [page](https://ebelge.gib.gov.tr/earsivyararlanmayontem.html)
- [S7] VUK (Law 213) Madde 253, 254 (official text read): [PDF](https://www.mevzuat.gov.tr/MevzuatMetin/1.4.213.pdf)
- GIB e-Belge site index of guides, schemas, portal and test links: [ebelge.gib.gov.tr](https://ebelge.gib.gov.tr)

## 1. The three ways to issue e-Arsiv Fatura

S1 V.1 and S3 name exactly three methods; a taxpayer may use any one, and may also use a different method for e-Arsiv than the one used for e-Fatura:

1. **GIB Portal method.** Free web portal run by GIB for taxpayers without the IT capacity to integrate (S1 V.1.1; S3 states "UCRETSIZ"). Portal URL listed on the GIB site: [earsivportal.efatura.gov.tr](https://earsivportal.efatura.gov.tr/) (test portal: earsivportaltest.efatura.gov.tr).
2. **Ozel entegrator (licensed private integrator).** A company licensed by GIB creates the invoice and the e-Arsiv report, applies the seal and time stamp, delivers to the buyer, and sends the report to GIB (S1 V.1.2). No separate application to GIB is needed by the taxpayer for this method (S3). GIB publishes the licensed integrator list at [earsivozelentegratorlistesi.html](https://ebelge.gib.gov.tr/earsivozelentegratorlistesi.html) (list is loaded dynamically; I did not read its contents).
3. **Direct integration (own IT system).** The taxpayer's own system integrates with GIB. S6 describes it for taxpayers that "operate their own system 7/24" with adequate software and hardware. Applied for by post (S3), tested on GIB's test web service, then approved by GIB in writing (S2 sections 12-13).

Other facts:
- Using an integrator does not remove the taxpayer's own retention and production duty (S1 V.1.2 and S2 section 1).
- Integrator systems (hardware/software used to issue) must be located inside Turkey (S1 V.1.2).
- Integrator can be switched; for e-Arsiv the switch is possible at month end, and a month's packages must be sent wholly through the integrator that started the month (S2 section 5).
- The taxpayer may ask that the integrator's own seal and time stamp be used on documents and reports (S1 V.1.2).

**[ACCOUNTANT]**: choice of method. NOT CONFIRMED: whether the shop is already in e-Fatura/e-Arsiv (research 04 notes the entity status is unknown).

## 2. GIB Portal method

- Application: electronic, with a mali muhur or e-imza (qualified e-signature) from an authorised provider, a local Java signing tool, Chrome or Firefox, via the "GIB Portal Yontemi" button on ebelge.gib.gov.tr; success notice comes by e-mail (S4, 11 steps).
- The portal creates e-Arsiv Fatura, e-SMM and e-MM. Portal users have no duty to build or upload e-Arsiv reports; GIB does it (S2 section 5).
- The portal user may only use the "GIB" unit code (series prefix); integrators and direct-integration users may not (S2 section 6).
- Trade-off facts from a secondary vendor article (Parasut blog, updated 2026-02-03, NOT a primary source): the portal is free; invoices are archived on the portal for 6 months; the portal cannot deliver the invoice to the buyer (download and send yourself); a signed invoice cannot be edited or cancelled in the portal; a cap of "at most 500 e-Arsiv invoices" is stated with no period; login by interactive tax office credentials plus "GIB Imza" SMS code with no mali muhur needed. [Parasut: GIB portal](https://www.parasut.com/blog/gib-portaldan-e-arsiv-fatura-kesmek). **CONFLICT**: this login description differs from the 2019 GIB application document (S4, which requires mali muhur or e-imza). NOT CONFIRMED which is current; the 6-month archive, the 500 cap and the no-cancel claim are NOT CONFIRMED in GIB text I read. **[ACCOUNTANT]** to confirm with the portal itself.
- Bulk or API issuance through the portal: NOT CONFIRMED (no GIB text found for it). The portal is a manual web interface (S1 V.1.1: "interfaces offered inside the portal").
- No automation path from a Django order to the portal was found in GIB documents.

## 3. Ozel entegrator method: cost and API

- Cost: GIB does not set prices. Prices I found are vendor-published and may be outdated; treat as NOT CONFIRMED and get quotes. Model seen across vendors is a per-document "kontor" (credit) bundle, sometimes with a monthly package.
  - Nilvera 500 kontor for 1,312 TL and 1,000 kontor for 2,375 TL (ideasoft partner-campaign price, 50 percent off; date unknown): [ideasoft page](https://www.ideasoft.com.tr/eticaret-cozum-merkezi/earsiv-efatura-cozumleri/nilvera/).
  - Parasut says no integration fee but kontor needed; a third-party page lists packages from 79 TL/month (50 e-Arsiv) to 499 TL/month (1,000), API access in the 250-kontor package; not verified against Parasut's own price page. [Parasut kontor note](https://www.parasut.com/blog/kobiler-icin-ucretsiz-e-fatura).
- API existence (first-party developer docs):
  - Nilvera: REST API, OAuth 2.0 style bearer API key created in the portal with chosen scopes and expiry; separate test host `apitest.nilvera.com` and production `api.nilvera.com`; e-Arsiv Fatura listed as an integrable service; endpoint details not shown on the landing page I read. [Nilvera developer](https://developer.nilvera.com)
  - Parasut: v4 REST API, OAuth2 authorisation-code flow with refresh, test environment, API application form; the landing page shows only `POST /v4/sales_invoices` and does not mention e-Arsiv. [Parasut DevHub](https://devhub.parasut.com/)
  - BirFatura offers an API for order-to-invoice automation (vendor page; not read in detail): [BirFatura API](https://birfatura.com/birfatura-api-entegrasyonu/).
  - Other integrators exist (Uyumsoft, Sovos/Foriba, Logo e-Logo, izibiz, etc.); I did not read their API docs. Sovos documents an "Issue invoices in e-Arsiv" flow: [Sovos docs](https://docs.sovos.com/en/indirect-tax/indirect-tax-products/einvoicing/compliance-network/country-setup-guides/turkey/issue-invoices-in-e-ar%C5%9Fiv) (not read).
- Whether any integrator API lets a custom Django app create e-Arsiv from an order (endpoints, fields, price tier needed): NOT CONFIRMED beyond the above; must be checked against the chosen vendor's API docs.

## 4. Direct integration: technical requirements (S2)

- Invoice format: UBL-TR with `ProfileId` = `EARSIVFATURA`; signed with XAdES-BES using mali muhur or NES (S2 section 6). PDF is allowed only by special permission, signed PAdES, with the UBL-TR XML attached and `CopyIndicator` = true.
- Delivery to buyer: if sent electronically the message must explain how to view UBL-TR, how to verify the signature, and how to print (esp. for internet-sale returns), and point to GIB's e-fatura viewer. If sent on paper, the printout must say where the invoice can be downloaded electronically and from which date it can be queried at GIB (S2 section 6).
- e-Arsiv report: build per the `eArsiv.xsd` schema in the GIB package, sign with XAdES-A using mali muhur or e-imza with time stamp, send via SOAP web service (WSDL in the package) (S2 sections 4-5). GIB packages and schemas: [earsiv_paket_v1.20.zip](https://ebelge.gib.gov.tr/dosyalar/kilavuzlar/earsiv_paket_v1.20.zip) (not opened).
- Report timing: since 1/1/2019 reports are daily periods and must be sent at the latest by the end of the following day (S2 section 5). Earlier monthly rule (by the 15th of next month) was the pre-2019 rule.
- Web service: methods `sendDocumentFile`, `getBatchStatus`, `getUserList`; file name is a UUID, zipped, one same-named XML inside, max 100 MB uncompressed; WS-Security signing of SOAP Timestamp and Body with mali muhur or NES; status and error codes in S2 section 11 (S2 section 5).
- Test: test WSDL `https://test.efatura.gov.tr/earsiv/services/EArsivWsPort?wsdl`; GIB reviews test usage and sample reports and then approves in writing (S2 sections 12-13).
- GIB may require FTP transfer of invoices to its systems for named groups, with written notice (S2 section 14, S1 V.8).
- Invoice number: internet sales must use a unit code (series) specific to internet sales, separate from other sales (S2 section 6).
- Reporting does not remove retention and production duties (S2 section 1; S1 V.8).

## 5. Invoice content for an internet sale

Mandatory content of any e-Arsiv Fatura (S1 IV.2.3): issue date and document number; issuer name, trade name if any, business address, tax office, VKN; buyer name/surname or trade name, tax office and VKN "if any" (this is how the text reads: "varsa"); goods kind, quantity, price, amount, tax type, rate and amount; delivery date and waybill number of the goods; GIB-defined QR code or barcode from a date to be announced on ebelge.gib.gov.tr.

Extra content for internet sales (S1 IV.2.4.5), also covered in research 04: web address of sale; payment method; payment date; carrier name and VKN/TCKN (goods); dispatch date; return section (returner's name, address, signature, item type, quantity, unit price, amount); and the sentence "Bu satis internet uzerinden yapilmistir." The invoice must be delivered electronically; a copy of the printout, ÖKC slip or waybill must travel with the goods. A returned, signed printout stands in for a gider pusulasi.

How these map to the report schema `internetSatisBilgi` (S2 3.3.2.17): `webAdresi` mandatory; `odemeSekli` mandatory with values KREDIKARTI/BANKAKARTI, EFT/HAVALE, KAPIDAODEME, ODEMEARACISI, DIGER; `odemeTarihi` mandatory for EFT/HAVALE, optional for KAPIDAODEME or DIGER; `odemeAracisiAdi` optional; `gonderiBilgileri` mandatory with `gonderimTarihi` and `gonderiTasiyan` (carrier VKN and title, or the seller's own details when it delivers itself). `gonderimSekli` is KAGIT or ELEKTRONIK. Other report fields include `faturaNo`, `faturaUUID`, `faturaTip`, `duzenlenmeZamani`, `toplamTutar`, `odenecekTutar`, `paraBirimi`, per-line `kalemDetay` (name, unit price, quantity, taxes), `vergiBilgisi` and `imzaZamani`.

Facts that matter for this shop:
- COD and bank transfer both exist as payment-method values; for COD the payment date is optional in the report (S2 3.3.2.17.2-4). Whether a COD sale also needs a cash-register (yazar kasa) receipt: NOT CONFIRMED, **[ACCOUNTANT]** (already flagged in research 04).
- The payment date for a bank transfer must be known when the invoice is made, so the order has to be paid first for that method (follows from the mandatory field above).

## 6. Buyers without a tax ID (consumers)

- e-Arsiv Fatura is the required form for sales to buyers not registered in e-Fatura, including non-taxpayers (S1 IV.2.1, S3).
- Buyer VKN/TCKN on the invoice is "if any" in S1 IV.2.3. In the report schema, `aliciBilgileri` is mandatory, with `gercekKisi` (`tckn`, `adiSoyadi`) or `tuzelKisi` (`vkn`, `unvan`) (S2 3.3.2.16).
- Placeholder TCKN `11111111111` (and VKN `2222222222`) for consumers who give no ID: the only source I found is a search summary of a GIB ruling (ozelge no. 27575268-105[Muk.242-2020-907]-191430, 04.05.2023, [Lexpera](https://www.lexpera.com.tr/mevzuat/ozelgeler/27575268-105-muk-242-2020-907-191430-sayili-ozelge-1)); the ruling's conclusion is behind a paywall, and the visible part shows an online seller entering "1111 1111 111" for customers who left the ID blank. The summary says the field cannot be left empty technically and that the placeholder is not enough where the buyer's ID is mandatory (for example a business buyer). NOT CONFIRMED in a GIB primary text. **[ACCOUNTANT]**.
- Whether the shop may require a TCKN at checkout is a KVKK matter, not decided here (see research 04 KVKK section and the checkout consent ticket).
- Sales to a business buyer need the buyer's VKN; whether the buyer is in e-Fatura decides e-Fatura vs e-Arsiv (S1 IV.2.1).

## 7. Cancellation, returns and corrections

- Cancellation of an e-Arsiv Fatura must be done through the GIB system and within 8 days of the document being delivered to the buyer; either party starts the request (S5 section 3). The 8 days follows Turk Ticaret Kanunu Madde 21(2) (invoice is deemed accepted after 8 days without objection, quoted in S5).
- Objections raised outside the system (notary, registered mail, telegram, KEP) must also be reported into the GIB system (S1 V.10; S5).
- Itiraz acceptance deadline: acceptance by the issuer by the 15th day end of the month following the document's month keeps it out of the buyer's Ba and the seller's Bs forms (S5 sections 4.2-4.4, amended 03.01.2025).
- Report schema has `faturaIptal` (cancelled invoices) and `faturaitiraz` elements (S2 3.3.2.27, 3.3.3).
- How a return after the 8 days is corrected (iade, gider pusulasi, credit note): partly covered by S1 IV.2.4.5 (signed returned printout replaces the gider pusulasi). Credit-note handling for e-Arsiv returns beyond that: NOT CONFIRMED, **[ACCOUNTANT]**.

## 8. Retention

- Electronic e-Belge copy must be kept in the issuer's own electronic, magnetic or optical media, with the mali muhur or e-imza, and presented on request; printing and keeping on paper does not satisfy it (S1 VI). Storage with another taxpayer or licensed storage provider is allowed but the taxpayer stays responsible. Storage must be inside Turkey and under Turkish law; a secondary archive abroad is allowed (S1 VI).
- Period: VUK Madde 253 gives 5 years from the start of the calendar year following the relevant year for those required to keep books and their documents; Madde 254 gives the same 5 years for those not required to keep books (S7, official text read). S1 VI refers to "legal periods" without stating a number. Longer periods may apply under other laws (for example TTK Madde 82, 10 years for commercial books and documents): NOT CONFIRMED, not read in this pass. **[ACCOUNTANT]**.
- Retention duty covers the data, verification tools and ability to display and print, not just the PDF (S1 VI).
- A secondary source claims invoices are kept for 6 months on the GIB portal; if true, the seller must download its own copies (see section 2, NOT CONFIRMED).
- Query of issued invoices by anyone: [e-Arsiv Fatura Sorgulama](https://ebelge.gib.gov.tr/earsivsorgula.html) (page listed on the GIB site; not read).

## 9. Accountant-workflow (export-based) facts

- GIB documents I read describe no CSV or Excel upload format for the portal and no GIB-defined export format other than the UBL-TR XML and the report XML. NOT CONFIRMED that the portal accepts bulk upload. Sections 3 and 4 above are the paths that accept machine-generated data (integrator API or direct web service).
- Per-invoice data an external party needs to make a legally complete e-Arsiv from an export is the S1 IV.2.3 and IV.2.4.5 content in section 5, plus the buyer details and the carrier (name and VKN/TCKN), dispatch date and payment date and method. Which of these the shop's order data must therefore capture is a design question for later tickets, not decided here.
- Whether the accountant's own software or integrator can import orders from CSV: depends on the vendor; not researched. **[ACCOUNTANT]** to say which integrator or software they use.

## Needs an accountant (summary)

1. **[ACCOUNTANT]** Entity status: already in e-Fatura/e-Arsiv or not; tax regime; which method (portal, integrator, direct); which integrator the accountant already works with.
2. **[ACCOUNTANT]** Current GIB Portal login and limits (conflict in section 2); whether portal use fits expected order volume and whether portal-issued invoices need own downloaded copies.
3. **[ACCOUNTANT]** Placeholder TCKN rule for consumers who give no ID (section 6).
4. **[ACCOUNTANT]** COD: yazar kasa or ÖKC slip needed or not; invoice timing for COD and unpaid bank-transfer orders (invoice needs payment date for EFT/HAVALE).
5. **[ACCOUNTANT]** Return and cancellation after 8 days; retention period beyond the 5 years in VUK 253/254.

## Limits of this research

- Read in full as official text: S1 relevant sections, S2, S3, S4, S5, S7. Not read: the e-Arsiv UBL-TR invoice schema/package (zip), the integrator list contents, the GIB portal itself, and GIB's e-Arsiv SSS forum.
- GIB documents do not give any price. All price figures are vendor-published and unverified.
- Integrator API depth (endpoints, payloads, limits) was read only at landing-page level for Nilvera and Parasut; other vendors not read.
- Secondary sources used (vendor blogs, a Lexpera summary, a search-engine summary of a ruling) are labelled as such where used.
