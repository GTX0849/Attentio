# ClassPulse: Classroom Design Document

Ticket: CLA-7

## 1. Purpose and scope

ClassPulse measures how attentive each student is during a lecture. A camera worker classifies each student's attention state, a FastAPI backend stores 30-second records in PostgreSQL, and a Streamlit dashboard reports to students, teachers and administrators.

This week's deliverable is a webcam prototype: one face, a fixed student ID, no face recognition and no logins. This document explains how the prototype extends to a real classroom. It also lists the deferred features and the limits of what the system can claim. The prototype has been tested only by acting out cases in front of a webcam. The classroom design below has not been built or tested, and nothing here is a result.

## 2. Prototype and classroom compared

Only the capture source and the identity step change. The backend, database and record format stay the same.

| Part | Prototype (webcam) | Classroom version |
| --- | --- | --- |
| Camera | Laptop webcam | Fixed classroom camera |
| Faces in view | One | Whole class |
| Student identity | Fixed student ID | Face recognition against enrolled faces, keyed by register number |
| Face not visible | Shoulders visible or not | Seat regions plus a torso or person detector |
| Worker location | The laptop | A machine next to the camera |
| Calibration | 3 seconds looking at the screen | Calibrated to the board's position |
| Timetable | Weekly CSV upload | Import from the university ERP |
| Access | No logins | Real logins with three roles |
| Dashboard | Streamlit | Streamlit; React deferred |

## 3. What the prototype already decides

The classroom design keeps these decisions unchanged.

### 3.1 Attention states

The worker assigns one of four states to each frame, at about 10 frames per second. The checks run in priority order: face, then eyes, then head direction. Pitch and yaw are measured relative to a 3-second calibration.

- **Attentive:** pitch from -10 to +15 degrees, yaw within 25 degrees.
- **Neutral (note-taking):** pitch above +15 and up to +38 degrees, yaw within 25 degrees.
- **Distracted:** anything beyond those bands.
- **Not visible:** the face and the shoulders are both lost.

Three further rules apply. Eyes closed for 1.5 seconds or more count as Distracted from that point on. Looking down is Neutral for up to 8 seconds and then Distracted, and the allowance refills only after 2 seconds of continuous Attentive. If the face is lost but the shoulders are visible, the state is Distracted.

### 3.2 Records, scores and lectures

The worker produces one record per student per 30-second window, holding the seconds spent in each state. Longer periods are sums of these records.

- **Attention** is attentive seconds divided by (attentive plus distracted seconds).
- **Presence** is a separate figure. Attention shows as "insufficient data" when presence is below 50%, and a student is present for a lecture at 50% presence or more.
- The backend resolves the lecture from the camera's room and the time, using a weekly timetable CSV. A manual session overrides the timetable.

### 3.3 Teacher figures and marks

- The headline teacher figure is self-comparison: each student's attention in this teacher's lectures minus their own average, averaged over the class. A second figure is slot-adjusted.
- Both are shown with a 95% range and hidden below 5 lectures. Results are feedback, not a ranking.
- Teachers upload marks as CSV or Excel. Marks become percentiles within each test, and students are sorted into four groups by attention and marks.
- A simulator generates a simulated cohort with known effects, so the figures can be checked against effects we put in ourselves.

## 4. Face recognition

### 4.1 Library choice

We will use InsightFace with the buffalo_l model pack, which provides face detection and ArcFace embeddings. We chose it over dlib's `face_recognition` because it is more accurate on side profiles and at low resolution, both common for a camera at the front of a room, and because it runs on a GPU. We have not benchmarked the two ourselves, so the choice should be checked on a recording from a real room.

### 4.2 Enrolment

Each student enrols once, after giving consent (section 8). We capture a few images from different angles, compute an embedding for each, and store the embeddings against the student's register number. The images themselves are not kept. The register number is also the key for marks and the timetable, so all data joins automatically, and matching gives automatic attendance.

### 4.3 Matching and unknown faces

For each detected face, the worker computes an embedding and compares it by cosine similarity with the embeddings of the students enrolled in that lecture's class. Searching only that class lowers the chance of a wrong match.

A face is accepted only if its best similarity is above a threshold and clearly better than the second best. We have not fixed the threshold. We will set it in a test with enrolled volunteers, choosing a value that keeps wrong matches very rare even if some correct matches are missed. A missed match is the safer error, because a wrong match would put one student's data in another's record.

A face below the threshold is labelled unknown. Unknown faces create no record and nothing about them is stored. The worker tracks a face across frames, so an identity confirmed once is carried forward and uncertain frames are retried.

### 4.4 Re-enrolment

Re-enrolment is needed when matching keeps failing for a student, when their appearance changes a lot, or when they rejoin after withdrawing. New embeddings replace the old ones, and the old ones are deleted.

## 5. Seat regions, torso detection and occlusion

### 5.1 Splitting "face not visible"

In the prototype, a missing face has two outcomes, Distracted or Not visible. In a classroom that is too coarse. An empty seat, a blocked view and a lowered head all look the same to a face detector, and they must not be treated alike.

At set-up, each seat gets a region in the camera image, drawn once on a still frame. A torso or person detector runs alongside the face detector. When no face is found for a seat, the worker checks the seat region.

| Situation | Detection | Treatment |
| --- | --- | --- |
| Seat empty | No person or torso in the seat region | Excluded from attention; counts toward presence only |
| Line of sight blocked | Another person or object covers the seat region | Excluded from attention |
| Seated, face lowered or turned away | Torso present, face not found | Distracted |

### 5.2 Occlusion

Occlusion is the main risk of wrong labels in a real room.

- **The teacher walking past.** The teacher is not an enrolled student, so a person detected in front of a seat is treated as blocking it. The window is excluded as "line of sight blocked", and such blocks are usually short.
- **A student in front.** A front-row student can hide the one behind. This is a fixed geometry problem. At set-up we check which seats the camera cannot see well. Students in those seats should be reported as having limited data, not as distracted.

The worker should not guess when unsure. A seat that is blocked for much of a lecture falls below the 50% presence rule and shows "insufficient data".

## 6. Cohort synchronisation

Students often look down together, for example to copy from the board. Judging each student alone would label the whole class Distracted after 8 seconds. Two rules use the state of the class to adjust the individual allowance.

- **Class looks down together.** If 30% or more of the class looks down within 5 seconds, the Neutral allowance is boosted, because the class is probably doing a task the teacher set. The size of the boost is a parameter we have not yet chosen.
- **One student looks down while the class is attentive.** If 85% of the class is attentive while one student looks down, that student's grace is shortened from 8 seconds to 3 seconds.

Both rules change only the allowance in section 3.1, not the pitch and yaw bands. They need the camera to see most of the class, so they should switch off when too few faces are in view. The thresholds were chosen by reasoning, not from classroom data, so we should check them against recordings before relying on them.

## 7. Logins and access

The classroom version adds real logins and keeps the prototype's per-role data limits.

| Role | Can see | Cannot see |
| --- | --- | --- |
| Student | Only their own data | Anyone else's data |
| Teacher | Only their own lectures and figures | Other teachers' lectures or figures |
| Admin | All teachers' figures, listed alphabetically | Per-student attention |

The alphabetical order is deliberate, so that the list is not read as a ranking. These limits are enforced in FastAPI, not only in the dashboard, so replacing Streamlit later does not weaken them. People sign in with an email and a password, and the role comes from the account. A student's login is linked to their register number and a teacher's to their name in the timetable. The prototype implements this: see Signing in in the README.

## 8. Consent and privacy under India's DPDP Act

Face data is biometric personal data, so the Digital Personal Data Protection Act requires explicit consent. We have not had this design reviewed by a lawyer. The university's data protection officer should review it before any classroom use.

- **Consent and enrolment.** No student is enrolled without a recorded consent. The consent text says what is collected, why, who can see it, how long it is kept and how to withdraw. We store the date and the version of the text the student saw.
- **Data minimisation.** Frames are processed on the worker and discarded immediately. No video or image is written to disk or sent to the backend. Only face embeddings and aggregate scores are stored.
- **Transparency.** Students can see their own data in the dashboard.
- **Retention and deletion.** These are proposals for the university to confirm. Embeddings are kept while the student is enrolled and deleted on withdrawal of consent, on leaving the programme, or on re-enrolment. The 30-second records are kept for the semester and then deleted or reduced to lecture totals. A withdrawal request deletes the student's embeddings and records, and the deletion is logged without keeping the data. Backups follow the same schedule.

## 9. ERP timetable import

The classroom version replaces the CSV upload with an import from the university ERP, which is the source of truth for teacher, subject, room and hour. The import is a scheduled backend job that writes into the same timetable table the CSV fills now, so the lookup from room and time to lecture does not change. A manual session still overrides the timetable.

Several points need confirming with the university first. We do not yet know what export or API the ERP offers. The register number must be the same in the ERP and in enrolment. Cancelled lectures, swapped teachers and holidays should be picked up on each import. The CSV upload should stay as a fallback.

## 10. Deferred features

These are outside the first classroom version.

- **Automatic trend alerts**, such as "attention in Maths dropped over three weeks". Approach: compute each student's weekly attention per subject from the stored records, and alert only when a fall is sustained and larger than normal week-to-week variation.
- **Regression-based value-added teacher adjustment.** Approach: regress attention on teacher, slot, subject, day and student, and report the teacher effect with a confidence interval. It needs many lectures per teacher, and it has the limit described in section 11.
- **Anonymous student ratings.** Approach: store no student ID and keep the data apart from face data. Show class-level results only, and only with at least 10 responses. Release them after marks are final. Ask about the lecture (for example, whether the pace was too fast, right or too slow), not the teacher. Give less weight to students who rate everything the same.
- **WebSocket live updates.** Approach: add a FastAPI WebSocket endpoint that publishes each new record, with the same role checks as the REST endpoints.
- **React dashboard replacing Streamlit.** Approach: build a React front end that calls the same FastAPI endpoints, with FastAPI unchanged. This suits separate logins and finer layout control.

## 11. Limits

- **Attention measures engagement, not understanding.** A student facing the board may be lost, and one looking down may be taking notes. That is why test marks are used for understanding.
- **Reading emotion from faces has weak scientific support.** ClassPulse does not attempt it. It uses head direction, closed eyes and face presence only.
- **The slot-adjusted teacher figure cannot separate a teacher from a slot they always teach in.** If a teacher always teaches at 2 pm, the slot effect and the teaching effect cannot be told apart.
- **Tests are infrequent.** Two or three tests per subject give an indication, not proof.
- **Results are feedback, not a ranking**, and should not be used to punish. Teacher figures need at least 5 lectures and carry a 95% range.

## 12. Hardware

**Camera.** A fixed camera at the front of the room, high enough to see faces over the heads in front. Placement decides which seats are visible and at what angle, so it should be chosen by testing in the actual room. A wide view helps coverage but reduces pixels per face, which hurts recognition at the back. Large rooms may need more than one camera, which we have not tested.

**Calibration to the board.** In the prototype, the student looks at the screen for 3 seconds. In a classroom, "looking at the board" means a different head angle from each seat. Calibration will set the reference pitch and yaw from the board's position as seen from each seat region. The bands in section 3.1 stay the same, and only the reference changes.

**The machine next to the camera.** The worker runs on a computer in the room and needs a GPU for InsightFace and the person detector. It sends only scores to the backend, so the network carries small records and no video. We have not yet measured which GPU can handle a full class at about 10 frames per second.
