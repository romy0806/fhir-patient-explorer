# FHIR Patient and Condition Explorer

I wanted hands-on practice with FHIR, the standard format healthcare systems use to exchange patient data, so I wrote a
small Python script that pulls Patient and Condition records from the public HAPI test server, turns the nested JSON into
tables, and checks the data for problems.

This is a learning project. It uses a public test server with no real patient data, and it is not a clinical analysis.
I used AI tools (Claude, ChatGPT) to help write the code, then ran it, checked the output and wrote up what I found.

## What the script does
- Requests Condition records from the HAPI FHIR R4 server (two pages of 50) and follows the paging links safely.
- Picks up to 20 patients referenced by those conditions and fetches each one.
- Flattens the JSON into pandas tables and joins patients to their conditions.
- Checks for missing or invalid birth dates, unknown gender and missing condition codes.
- Saves the results as CSV files.

## What I learned about FHIR
- Data comes as resources (Patient, Condition), and search results arrive in a Bundle.
- A Condition points to its patient through a reference like `Patient/123`. That reference is what lets you join the tables.
- Big searches return a link to the next page instead of everything at once, so the code has to follow it.
- A condition code only means something together with its code system, such as SNOMED CT.
- One patient can have several conditions, so a join repeats the patient on each row.

## Results from one run
| | |
|---|---|
| Condition records sampled | 100 |
| Patients retrieved | 20 of 20 |
| Conditions matched to those patients | 26 |
| Patients with missing birth date | 0 |
| Patients with unknown gender | 1 |

## What I found in the data
The most common condition code was `persistent-cough`, shared by 15 of the 20 patients. It comes from a code system called
`chartr.example/synthetic-workflow`, which is not a real medical standard, and it has no display text. The same 15 patients
also share one birth date. That looks like a single bulk-generated test upload, not 15 independent patients, which leaves
only about 5 distinct patients in my sample.

So I did not treat the cough count as a finding about patients. I treated it as a data quality problem. The HAPI server is
open to anyone, so nothing on it is curated or verified.

The real SNOMED codes were rare: hypertensive disorder showed up for 3 patients and the rest appeared once. Some display
names were in Spanish, which is a good reminder that terminology text varies by source.

## Limits
- It is a tiny sample from an uncurated server, so it says nothing about real patients.
- Patients were picked from the first conditions the server returned, so only people with at least one recorded condition
  could appear, and the order was not random.
- It covers only Patient and Condition. There is no lab, encounter or medication data.
- Conditions come from the sampled pages only, not each patient's full history.
- I did not validate codes against SNOMED CT or ICD-10. I only checked that a system and a code were present.

## If I extended this
I would drop the bulk cohort and rerun on the remaining patients, add Observation resources such as labs and vitals, and
try a larger synthetic FHIR dataset generated with Synthea.

## Run it
```bash
pip install requests pandas
python fhir_patient_explorer_linked.py
```
The CSVs are written to an `output_linked/` folder, which is not included here, so run the script to create it. The test
server changes over time and can be slow, so your results will differ from mine.
