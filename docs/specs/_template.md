# Spec: [Feature Name]

**Status:** Draft | Ready | In Progress | Shipped | Cancelled
**Created:** YYYY-MM-DD
**Author:** [name]

---

## 1. Problem Statement

> What problem does this solve? Who is affected? Why now?

---

## 2. Goals

- G1: [measurable goal]
- G2: [measurable goal]

## 3. Non-Goals

- [explicitly out of scope — prevents scope creep]

---

## 4. User Stories

- As a **[user type]**, I want **[action]** so that **[outcome]**

---

## 5. Acceptance Criteria

- [ ] AC1: [verifiable, testable criterion]
- [ ] AC2: [verifiable, testable criterion]

---

## 6. UI / UX Mockup

Describe layout with ASCII diagrams. Add image paths if screenshots exist.

```
┌─────────────────────────────────────────────┐
│  [Component name]                           │
│                                             │
│  [Describe what goes here]                  │
│                                             │
└─────────────────────────────────────────────┘
```

### States to cover
- Default / empty state
- Loading state
- Error state
- Success state

---

## 7. Technical Design

### Backend changes
- New endpoint: `METHOD /api/path` — description
- Modified: `file.py` — what changes

### Frontend changes
- New component: `ComponentName.tsx` — description
- Modified: `PageName.tsx` — what changes

### Data shape
```ts
interface NewType {
  field: type;
}
```

---

## 8. Security Considerations

- [ ] Input validated at API boundary
- [ ] No path traversal risk
- [ ] No prompt injection risk (if LLM involved)
- [ ] No secrets in response

---

## 9. Open Questions

- [ ] Q1: [question that needs answering before implementation]

---

## 10. Implementation Notes

*(filled in during/after implementation)*
