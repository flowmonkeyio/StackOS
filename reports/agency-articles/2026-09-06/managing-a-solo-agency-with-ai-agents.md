# Managing a solo agency with AI agents

One client can have a website project in final review and a campaign waiting for a different decision. Each has its own scope, deadlines, and unfinished work. An agent reading “everything about this client” needs a way to tell those engagements apart.

Here is a proposed arrangement for a solo agency: keep a short agency brief, give each active engagement its own working record, and use a coordinating agent for decisions that cross projects. Finance serves the company across those engagements. Each job should specify what the agent reads, the work it may carry out, and what it must report back.

## Give the agency agent a short business brief

The agency brief should answer agency questions: who the business is, how the owner wants work handled, the current engagements, and commitments that affect more than one of them. It should say which decisions the owner retains, such as changing a price or promising an earlier delivery date.

It should not hold every scope document, draft, message, and old decision. When material matters only to one piece of client work, it belongs with that engagement.

An agency agent could use that brief to review upcoming commitments and identify where the owner needs to make a decision. The project records would remain the source for detailed status; the agency view would link to them instead of keeping a second account of every task.

The setup also needs working access and a way to start each job. For example, the owner could ask for a commitments review, or an agreed schedule could start it. The agent needs permission to read the selected project records and a place to deliver its findings. Writing those instructions does not itself connect the accounts or start a recurring process.

## Let each engagement carry its own active work

Consider a hypothetical client with two engagements: a site launch and a recruiting campaign. The launch agent reads that engagement’s agreed scope, current decisions, work due, and client inputs still needed. It can assemble a status, surface a missing approval, or prepare the next permitted item from the information it can see. Its return might say that copy is ready for approval, image selection remains open, and the launch date depends on that decision.

The campaign has its own brief and progress report. A question about its audience belongs there. Where the engagements depend on the same person's approval or compete for the owner's time, the project agent should raise that conflict for coordination.

Later work can start from accepted scope and unresolved questions instead of a generic client thread.

## Coordinate where commitments meet

Some work has no honest home inside a single engagement. A delivery date may affect another project. A client’s delayed input may change two commitments. The owner may need to choose which of several client conversations comes first.

In this arrangement, the coordinating agent reads the affected projects' reports and checks the commitments behind them. Suppose, in the hypothetical example, both engagements need a client decision before Friday, but the owner has time to prepare only one review. The agent should bring back the two deadlines, the work each review would unblock, and the decision needed. It should not silently move a promised date to make the plan fit.

After the owner chooses a priority, the agent can update the permitted working plan and check that the affected project records reflect the decision. Any client message or revised commitment still follows the owner's rules for that action.

This resembles a general orchestration pattern where a central agent delegates bounded work, synthesizes returns, and brings judgment back to a person. [Anthropic’s overview of effective agents](https://www.anthropic.com/engineering/building-effective-agents) describes that pattern; it is not proof that this agency design will work. More agents also add coordination overhead and failure paths, as [Microsoft’s orchestration guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) notes. A coordinator should appear when commitments truly cross, not because every business needs a fixed roster.

## Keep finance at the company level

Finance is shared business work. An agency can use one external financial source of truth across its engagements; a standalone business can use the same arrangement. Work such as [receipt intake](/library/workflows/finance-receipt-intake/) and [payment follow-ups](/library/workflows/finance-payment-request-followups/) belongs here, even when an item relates to a particular project.

A [bookkeeping preparation agent](/library/agents/stackos-finance-bookkeeping-preparer/) can prepare or check a financial record from the source it is authorized to consult and, when evidence supports it, associate that record with an engagement. The association is a label, not another financial master, a split allocation, or permission to guess. A company-wide expense stays company-wide without a source-backed engagement. If it might relate to several projects, the uncertainty returns as a question.

The same client can have several engagements, so a customer name alone is not enough to establish financial attribution. Agency context also does not grant access to a client’s books or combine separate businesses into one financial picture. The financial source, the engagement reference, and the authority to act each need to be resolved independently.

## Use the next item to test its placement

Before naming another agent, take one real piece of work and ask where it belongs. If it concerns one engagement’s scope, state, or outstanding decision, begin there. If it compares commitments across engagements, bring it to the agency coordination point. If it changes or prepares a company financial record, keep the source of truth at the business level. Client-owned data or work for a separate business needs separately defined scope and access.

Then make the operating question concrete: what may the agent read, what may it do, what must it return, and which decision still belongs to the owner? Prompts alone will not answer those questions. The arrangement starts to earn its place when a real item can enter through a configured path, produce a checkable result, and leave an unresolved decision visible.

It will not fix commitments that were never made clear. The remaining test is smaller: can the next ambiguous item be placed without copying context, guessing attribution, or asking the owner to reconstruct work that already has a home?
