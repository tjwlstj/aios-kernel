"""Static syntax-only constraint for the private RESEARCH decision interface."""

GRAMMAR_ID = "static-decision-v1"
DECISION_GRAMMAR = r'''root ::= "{\"action\":" action-revision ",\"prediction\":" prediction ",\"attribution\":" attribution "}"
action-revision ::= set-revision | other-revision
set-revision ::= "\"SET\",\"expected_revision\":" revision
other-revision ::= other-action ",\"expected_revision\":null"
other-action ::= "\"OBSERVE\"" | "\"WAIT\"" | "\"FINISH\""
revision ::= [0-9] | "1" [0-9] | "2" [0-4]
prediction ::= "\"OBSERVED\"" | "\"APPLIED\"" | "\"STALE\"" | "\"DENIED\"" | "\"UNAVAILABLE\"" | "\"NOOP\""
attribution ::= "\"SELF\"" | "\"OTHER\"" | "\"UNKNOWN\""
'''
