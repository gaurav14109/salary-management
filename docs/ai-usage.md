How AI Was Used

AI wrote a significant portion of the code in this project. But the engineering decisions, trade-offs, and verification were mine.

My approach was simple: use AI to move faster, but never use it as a substitute for engineering judgment.

AI is good at generating code quickly. It is also capable of producing code that looks correct, passes a superficial review, and fails in production. The difference comes down to how the output is verified.

Where AI Helped

I used AI primarily to accelerate implementation and reduce the time spent on repetitive work:

Implementing code based on architectural and design decisions I had already made.

Writing test cases and validating that they actually detect regressions.

Running performance measurements and documenting the results.

Exploring the codebase, tracing dependencies, and identifying relevant implementation details.

The goal was not to delegate engineering responsibility. It was to spend less time on implementation overhead and more time on decisions that required judgment.

Where AI Did Not Make the Decisions

The architecture and product behavior were established before implementation.

Decisions such as the Backend-for-Frontend (BFF) pattern, the separation of the chat service, claims-based headers, and effective-dated salary records were made based on the requirements and system design.

AI was not responsible for defining what correctness meant for the application. That came from the requirements, data model, business constraints, and expected behavior.

I also did not accept generated code simply because it appeared reasonable. Every significant change had to be validated against the actual system.

How I Worked With AI

I rarely used prompts such as "build me a salary management application."

Instead, I described the requirement, the expected behavior, and the reasoning behind the decision.

For example:

I want the user token to be validated at the BFF itself. Authentication should be centralized, while authorization should be handled by the salary service.

For asynchronous file imports:

When a user imports a file, the processing should happen in the backend without making the user wait. The system should track whether the import is processing, completed, or failed, including the reason for failure. If another import is already processing, the subsequent file should wait in the queue.

For testing the chat service:

I have configured the Groq API key. I want to run the tests against the actual data in the database, not mocked data.

For frontend performance:

If the table contains 10,000 records, how are we rendering the data without impacting performance?

The difference is intentional. I describe the problem and the constraints rather than prescribing a library or implementation prematurely.

This gives the implementation context and makes it easier to evaluate whether the generated solution actually meets the requirement.