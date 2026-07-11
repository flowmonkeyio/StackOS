export type FlowTone = 'agent' | 'plan' | 'guard' | 'action' | 'evidence'
export type FlowStatus = 'waiting' | 'active' | 'done'

export interface ProductFlowStep {
  id: string
  eyebrow: string
  label: string
  detail: string
  tone: FlowTone
  code: string
  status?: FlowStatus
  statusLabel?: string
}

export interface ProductWorkflow {
  key: string
  shortLabel: string
  audience: string
  host: string
  request: string
  preset: string
  title: string
  description: string
  outcome: string
  steps: ProductFlowStep[]
}

export const productWorkflows: ProductWorkflow[] = [
  {
    key: 'content',
    shortLabel: 'Content',
    audience: 'Marketing & content',
    host: 'Claude Code',
    request: 'Turn our customer research into a four-week content campaign.',
    preset: 'Content campaign',
    title: 'Research to a ready-to-publish campaign',
    description:
      'Sources, brand voice, drafts, review, and publishing stay connected while your team keeps using the tools it already knows.',
    outcome: 'An approved campaign with every source, draft, decision, and published link still attached.',
    steps: [
      { id: 'content-context', eyebrow: 'Understand', label: 'Gather the source material', detail: 'Customer notes, brand voice, offer, and goals.', tone: 'agent', code: 'Context ready' },
      { id: 'content-plan', eyebrow: 'Plan', label: 'Shape the campaign', detail: 'Themes, channels, formats, and timing.', tone: 'plan', code: 'Plan ready' },
      { id: 'content-create', eyebrow: 'Create', label: 'Draft every asset', detail: 'Article, email, and social variants.', tone: 'action', code: 'Creating' },
      { id: 'content-review', eyebrow: 'Review', label: 'Collect feedback', detail: 'The right people review one clear set.', tone: 'guard', code: 'Approval' },
      { id: 'content-publish', eyebrow: 'Publish', label: 'Send it live', detail: 'Approved work moves into connected channels.', tone: 'evidence', code: 'Published' },
    ],
  },
  {
    key: 'commerce',
    shortLabel: 'Shopify',
    audience: 'Commerce',
    host: 'Gemini',
    request: 'Launch our fall collection across Shopify, email, and social.',
    preset: 'Collection launch',
    title: 'A coordinated collection launch',
    description:
      'Products, inventory, creative, storefront checks, and launch messages move as one job instead of five disconnected chats.',
    outcome: 'A live collection with pricing checked, inventory confirmed, creative approved, and launch results recorded.',
    steps: [
      { id: 'shopify-products', eyebrow: 'Products', label: 'Prepare the catalog', detail: 'Names, descriptions, images, and variants.', tone: 'agent', code: 'Catalog ready' },
      { id: 'shopify-stock', eyebrow: 'Inventory', label: 'Check stock and pricing', detail: 'Catch missing stock, prices, and shipping rules.', tone: 'guard', code: 'Checks passed' },
      { id: 'shopify-build', eyebrow: 'Storefront', label: 'Build the collection', detail: 'Create the collection and merchandising order.', tone: 'action', code: 'Building' },
      { id: 'shopify-review', eyebrow: 'Review', label: 'Preview the launch', detail: 'Mobile, desktop, links, and offers get reviewed.', tone: 'plan', code: 'Ready to approve' },
      { id: 'shopify-launch', eyebrow: 'Launch', label: 'Publish and announce', detail: 'Shopify, email, and social go live together.', tone: 'evidence', code: 'Launched' },
    ],
  },
  {
    key: 'finance',
    shortLabel: 'Finance',
    audience: 'Finance & operations',
    host: 'Claude Code',
    request: 'Prepare the monthly finance pack and flag anything unusual.',
    preset: 'Month-end review',
    title: 'A repeatable month-end review',
    description:
      'Exports, checks, questions, commentary, and signoff follow the same visible path every month without hiding judgment inside automation.',
    outcome: 'A review-ready finance pack with exceptions highlighted and every source file and approval easy to find.',
    steps: [
      { id: 'finance-collect', eyebrow: 'Collect', label: 'Bring in the source files', detail: 'Accounts, billing, payroll, and forecast exports.', tone: 'agent', code: 'Files collected' },
      { id: 'finance-match', eyebrow: 'Check', label: 'Match the numbers', detail: 'Compare totals, dates, and expected balances.', tone: 'guard', code: 'Reconciled' },
      { id: 'finance-flags', eyebrow: 'Review', label: 'Flag unusual changes', detail: 'Surface variances for a person to judge.', tone: 'plan', code: 'Questions ready' },
      { id: 'finance-pack', eyebrow: 'Prepare', label: 'Build the monthly pack', detail: 'Tables, commentary, and supporting files.', tone: 'action', code: 'Pack building' },
      { id: 'finance-signoff', eyebrow: 'Approve', label: 'Collect final signoff', detail: 'A human reviews before anything is shared.', tone: 'evidence', code: 'Approved' },
    ],
  },
  {
    key: 'engineering',
    shortLabel: 'Engineering',
    audience: 'Product & engineering',
    host: 'Codex',
    request: 'Fix the onboarding drop-off and prove the whole path works.',
    preset: 'Tracked delivery',
    title: 'A product change from request to release',
    description:
      'Requirements, dependencies, implementation, browser checks, review, and release remain one connected piece of work.',
    outcome: 'A reviewed release with the decision, code change, checks, screenshots, and follow-up state together.',
    steps: [
      { id: 'engineering-understand', eyebrow: 'Understand', label: 'Define the real problem', detail: 'Outcome, user flow, constraints, and evidence.', tone: 'agent', code: 'Scope clear' },
      { id: 'engineering-design', eyebrow: 'Design', label: 'Choose the approach', detail: 'Impact, dependencies, and edge cases.', tone: 'plan', code: 'Design reviewed' },
      { id: 'engineering-build', eyebrow: 'Build', label: 'Make the change', detail: 'Codex works in the repository as usual.', tone: 'action', code: 'In progress' },
      { id: 'engineering-test', eyebrow: 'Verify', label: 'Prove the full flow', detail: 'Tests and real browser paths back the result.', tone: 'guard', code: 'Checks running' },
      { id: 'engineering-release', eyebrow: 'Release', label: 'Close with evidence', detail: 'Review, release state, and next work agree.', tone: 'evidence', code: 'Ready to ship' },
    ],
  },
  {
    key: 'operations',
    shortLabel: 'Operations',
    audience: 'Customer & business operations',
    host: 'Gemini',
    request: 'Turn this week’s customer issues into owners, actions, and follow-ups.',
    preset: 'Weekly operations review',
    title: 'Customer signals to owned action',
    description:
      'Messages from different channels become a shared picture, then move through triage, ownership, follow-up, and reporting.',
    outcome: 'A weekly action list with owners, due dates, customer replies, and unresolved risks visible in one place.',
    steps: [
      { id: 'ops-collect', eyebrow: 'Collect', label: 'Bring feedback together', detail: 'Inbox, chat, support, and account notes.', tone: 'agent', code: 'Signals collected' },
      { id: 'ops-group', eyebrow: 'Triage', label: 'Group the real issues', detail: 'Duplicates merge and urgent items surface.', tone: 'plan', code: 'Priorities ready' },
      { id: 'ops-assign', eyebrow: 'Own', label: 'Assign each action', detail: 'Owners, due dates, and expected outcomes.', tone: 'guard', code: 'Owners confirmed' },
      { id: 'ops-followup', eyebrow: 'Respond', label: 'Close the loop', detail: 'Teams act and customers receive updates.', tone: 'action', code: 'Following up' },
      { id: 'ops-report', eyebrow: 'Learn', label: 'Keep the weekly picture', detail: 'Wins, open risks, and patterns remain visible.', tone: 'evidence', code: 'Review complete' },
    ],
  },
]
