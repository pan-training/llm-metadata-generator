# CourseInstance Profile

**Version:** 1.0-RELEASE (13 September 2022)
**Schema.org type:** `schema:CourseInstance`
**Profile URL:** https://bioschemas.org/profiles/CourseInstance/1.0-RELEASE

## Description

This specification can be used in tandem with a Course. A course is used to describe the broad, common aspects of a recurring training event - whereas a course instance is about the specific times and location of when that course is held.

## Properties

CD = Cardinality (ONE or MANY).
Marginality levels: **Minimum** (required), **Recommended**, **Optional**.

### Minimum Properties

| Property | Expected Type | CD | Description | Controlled Vocabulary |
|---|---|---|---|---|
| `@context` | URL | ONE | Used to provide the context (namespaces) for the JSON-LD file. Not needed in other serialisations. |  |
| `@type` | Text | MANY | Schema.org/Bioschemas class for the resource declared using JSON-LD syntax. For other serialisations please use the appropriate mechanism. While it is permissible to provide multiple types, it is preferred to use a single type. | Schema.org, Bioschemas |
| `@id` | IRI | ONE | Used to distinguish the resource being described in JSON-LD. For other serialisations use the appropriate approach. |  |
| `dct:conformsTo` | IRI | ONE | Used to state the Bioschemas profile that the markup relates to. The versioned URL of the profile must be used. Note that we use a CURIE in the table here but the full URL for Dublin Core terms must be used in the markup (http://purl.org/dc/terms/conformsTo), see example. | Bioschemas profile versioned URL |
| `courseMode` | Text / URL | MANY | The medium or means of delivery of the course instance or the mode of study, either as a text label (e.g. “online”, “onsite” or “blended”; “synchronous” or “asynchronous”; “full-time” or “part-time”) or as a URL reference to a term from a controlled vocabulary (e.g. https://ceds.ed.gov/element/001311#Asynchronous ). Bioschemas: The medium, means or pace of delivery of the course instance or the mode of study, either as a text label (e.g. “online”, “onsite”, “hybrid” or “blended”; “synchronous” or “asynchronous”; “full-time” or “part-time”) or as a URL reference to a term from a controlled vocabulary (e.g. https://ceds.ed.gov/element/001311#Asynchronous ). Another example of a Glossary of terms as defined by the Bioschemas Training community and published in Zenodo in 2016: https://zenodo.org/record/166378#.YrHnEi8iu-o |  |
| `location` | Place / PostalAddress / Text / VirtualLocation | ONE | The location of for example where the event is happening, an organization is located, or where an action takes place. Bioschemas: Location of the Course Instance. If the Course Instance is online, add the connection details as text |  |

### Recommended Properties

| Property | Expected Type | CD | Description | Controlled Vocabulary |
|---|---|---|---|---|
| `endDate` | Date / DateTime | ONE | The end date and time of the item (in ISO 8601 date format). | ISO 8601 |
| `inLanguage` | Language / Text | ONE | The language of the content or performance or used in an action. Please use one of the language codes from the IETF BCP 47 standard. See also availableLanguage. Supersedes language. |  |
| `instructor` | Person | MANY | A person assigned to instruct or provide instructional assistance for the CourseInstance. Bioschemas: An instructor can be the main teacher or trainer, as well as a training assistant, or a helper. |  |
| `offers` | Demand / Offer | MANY | An offer to provide this item—for example, an offer to sell a product, rent the DVD of a movie, perform a service, or give away tickets to an event. Use businessFunction to indicate the kind of transaction offered, i.e. sell, lease, etc. This property can also be used to describe a Demand. While this property is listed as expected on a number of common types, it can be used in others. In that case, using a second type, such as Product or a subtype of Product, can clarify the nature of the offer. Inverse property: itemOffered. Bioschemas: The price an attendee would pay to attend this CourseInstance. The price currency can be for instance in “GBP” (pound sterling) or “CHF” (Swiss francs). | Price currency ISO 4217 Date specified price ISO 8601 |
| `startDate` | Date / DateTime | ONE | The start date and time of the item (in ISO 8601 date format). | ISO 8601 |
| `url` | URL | ONE | URL of the item. Bioschemas: The preferred URL of this course instance. You must provide this value if it is known |  |

### Optional Properties

| Property | Expected Type | CD | Description | Controlled Vocabulary |
|---|---|---|---|---|
| `alternateName` | Text | MANY | An alias for the item. |  |
| `contributor` | Organization / Person | MANY | A secondary contributor to the CreativeWork or Event. Bioschemas: Contributors are those who made non-authorship contributions: e.g., critical review, commentary or revision. |  |
| `description` | Text | ONE | A description of the item. Bioschemas: A description of the Course Instance. (courseInstance) description can be used to override (course) description for specific course instances. |  |
| `duration` | Duration | ONE | The duration of the item (movie, audio recording, event, etc.) in ISO 8601 date format. Bioschemas: (CourseInstance) duration can be used to override (Course) duration for specific course instances. |  |
| `eventStatus` | EventStatusType | ONE | An eventStatus of an event represents its status; particularly useful when an event is cancelled or rescheduled. Bioschemas: An eventStatus of an event represents its status; particularly useful when an event is cancelled or rescheduled. Used as text label (e.g. “postponed”, “cancelled”, “date TBC”, “application open” or “registration closed”). |  |
| `funder` | Organization / Person | MANY | A person or organization that supports (sponsors) something through some kind of financial contribution. |  |
| `image` | ImageObject / URL | ONE | An image of the item. This can be a URL or a fully described ImageObject. |  |
| `maximumAttendeeCapacity` | Integer | ONE | The total number of individuals that may attend an event or venue. |  |
| `name` | Text | ONE | The name of the item. Bioschemas: The name of the course. Course instance name can be used to override Course name for variations in specific Course instances. Use name from Course unless the Course instance has a different name from the course. |  |
| `organizer` | Organization / Person | MANY | An organizer of an Event. |  |
| `subEvent` | Event | MANY | An Event that is part of this event. For example, a conference event includes many presentations, each of which is a subEvent of the conference. Supersedes subEvents. Inverse property: superEvent. Bioschemas: For events within events e.g. guest lecture within a workshop event |  |
| `superEvent` | Event | MANY | An event that this event is a part of. For example, a collection of individual music performances might each have a music festival as their superEvent. Inverse property: subEvent. Bioschemas: Use to describe the event a course instance takes place within. e.g. Galaxy Workshop during the ISMB Conference. |  |
| `workFeatured` | CreativeWork | MANY | A work featured in some event, e.g. exhibited in an ExhibitionEvent. Specific subproperties are available for workPerformed (e.g. a play), or a workPresented (a Movie at a ScreeningEvent). Bioschemas: The training material used in this specific course instance, or produced for this course instance. If this is the same for all Course Instances, use hasPart and/or mentions in Course instead |  |
