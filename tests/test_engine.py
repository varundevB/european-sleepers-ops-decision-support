"""Decision safety and workflow tests using fictional SQLite records only.

No operational workbook, passenger record, external account or network is needed.
"""

import sqlite3
import unittest

import engine
from import_data import ensure_data_schema


DAY = "2030-01-02"
AFFECTED = "71 901"
SPARE = "71 902"


class DecisionEngineTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        ensure_data_schema(self.conn)
        engine.ensure_case_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def add_car(self, car_id, owner="RDC", car_type="WL33", line="Milan",
                code="X", colour_meaning="", location=None):
        self.conn.execute("INSERT OR IGNORE INTO cars(id,owner,type) VALUES(?,?,?)",
                          (car_id, owner, car_type))
        self.conn.execute("INSERT INTO fleet VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (line, DAY, car_id, code, colour_meaning, "", location,
                           "Fictional test record", line, "H5"))
        self.conn.execute("INSERT OR IGNORE INTO line_days VALUES(?,?,?,?,?,?)",
                          (line, DAY, 90, "Fictional test service", line, 5))
        self.conn.commit()

    def prepare_case(self, line="Milan", product="4", affected_type="WL33",
                     spare_owner="RDC", spare_type="WL33", spare_id=SPARE,
                     spare_colour=""):
        self.add_car(AFFECTED, car_type=affected_type, line=line, code=product)
        self.add_car(spare_id, owner=spare_owner, car_type=spare_type, line=line,
                     colour_meaning=spare_colour, location="Denderleeuw")
        return engine.create_case(self.conn, {
            "line": line, "date": DAY, "car_id": AFFECTED,
            "reason": "Fictional workshop release changed; review alternatives.",
            "affected_passengers": 20,
        })

    @staticmethod
    def option(case, option_id="replace:" + SPARE):
        return next(option for option in case["options"] if option["id"] == option_id)

    @staticmethod
    def check(option, check_id):
        return next(check for check in option["checks"] if check["id"] == check_id)

    def confirm_pending(self, case, option_id="replace:" + SPARE):
        pending = [check["id"] for check in self.option(case, option_id)["checks"]
                   if check["status"] == "pending"]
        return engine.record_update(self.conn, case["id"], {
            "department": "Fictional coordination desk",
            "message": "Received and recorded the listed confirmations for this synthetic test.",
            "option_id": option_id, "confirmations": pending,
        })

    def approve(self, case, option_id="replace:" + SPARE):
        return engine.choose_decision(self.conn, case["id"], {
            "option_id": option_id, "rationale": "Synthetic decision after recorded review.",
            "approve": True,
        })

    def test_euroexpress_cannot_replace_a_milan_car_even_when_parked(self):
        case = self.prepare_case(spare_owner="EE")
        option = self.option(case)
        self.assertEqual(self.check(option, "country_approval")["status"], "fail")
        self.assertEqual(option["status"], "blocked")

    def test_gff_309_is_excluded_from_italy_for_its_actual_seating_product(self):
        case = self.prepare_case(product="2", affected_type="Bimz",
                                 spare_owner="Gff", spare_type="Bimz", spare_id="91 309")
        option = self.option(case, "replace:91 309")
        self.assertEqual(self.check(option, "product")["status"], "pass")
        self.assertEqual(self.check(option, "country_approval")["status"], "fail")
        self.assertEqual(option["status"], "blocked")

    def test_bdcm_country_restriction_blocks_an_otherwise_compatible_france_car(self):
        case = self.prepare_case(line="Paris", product="1", affected_type="Bvcmz6",
                                 spare_owner="Gff", spare_type="BDCM36")
        option = self.option(case)
        self.assertEqual(self.check(option, "product")["status"], "pass")
        self.assertEqual(self.check(option, "country_approval")["status"], "fail")
        self.assertEqual(option["status"], "blocked")

    def test_unlisted_owner_approvals_remain_unknown_not_approved(self):
        case = self.prepare_case(spare_owner="Fictional operator")
        option = self.option(case)
        self.assertEqual(self.check(option, "country_approval")["status"], "pending")
        self.assertEqual(option["status"], "conditional")
        with self.assertRaises(engine.APIError):
            self.approve(case)

    def test_pending_confirmations_allow_provisional_choice_but_prevent_approval(self):
        case = self.prepare_case()
        chosen = engine.choose_decision(self.conn, case["id"], {
            "option_id": "replace:" + SPARE,
            "rationale": "Review the reserve while waiting for operational confirmations.",
            "approve": False,
        })
        self.assertFalse(chosen["decision"]["approved"])
        self.assertTrue(chosen["drafts"])
        with self.assertRaises(engine.APIError):
            self.approve(chosen)

    def test_generic_confirmation_prose_never_resolves_checks(self):
        case = self.prepare_case()
        original_pending = {c["id"] for c in self.option(case)["checks"] if c["status"] == "pending"}
        updated = engine.record_update(self.conn, case["id"], {
            "department": "Fictional TCS desk",
            "message": "Everything is confirmed: technical release, shunting, bookings and costs.",
        })
        still_pending = {c["id"] for c in self.option(updated)["checks"] if c["status"] == "pending"}
        self.assertEqual(original_pending, still_pending)
        with self.assertRaises(engine.APIError):
            self.approve(updated)

    def test_explicit_received_evidence_resolves_only_the_named_check(self):
        case = self.prepare_case()
        updated = engine.record_update(self.conn, case["id"], {
            "department": "Fictional maintenance desk",
            "message": "Received technical release for the fictional replacement.",
            "option_id": "replace:" + SPARE,
            "confirmations": ["technical_release"],
        })
        option = self.option(updated)
        release = self.check(option, "technical_release")
        self.assertEqual(release["status"], "pass")
        self.assertIn("Received technical release", release["evidence"])
        self.assertEqual(self.check(option, "booking_match")["status"], "pending")
        self.assertEqual(option["status"], "conditional")

    def test_complete_explicit_review_enables_planner_approval(self):
        case = self.confirm_pending(self.prepare_case())
        self.assertEqual(self.option(case)["status"], "ready")
        approved = self.approve(case)
        self.assertTrue(approved["decision"]["approved"])
        self.assertEqual(approved["status"], "approved")
        # An approved coordination draft still does not claim it was sent.
        self.assertIn("Not sent", engine.handoff_text(approved))

    def test_unknown_country_approval_can_be_resolved_by_explicit_evidence(self):
        case = self.confirm_pending(self.prepare_case(spare_owner="Fictional operator"))
        self.assertEqual(self.check(self.option(case), "country_approval")["status"], "pass")
        self.assertTrue(self.approve(case)["decision"]["approved"])

    def test_source_backed_failure_cannot_be_confirmed_or_provisionally_selected(self):
        case = self.prepare_case(spare_owner="EE")
        with self.assertRaises(engine.APIError):
            engine.record_update(self.conn, case["id"], {
                "department": "Fictional desk",
                "message": "Attempted override of a prohibited country assignment.",
                "option_id": "replace:" + SPARE,
                "confirmations": ["country_approval"],
            })
        self.assertEqual(engine.get_case(self.conn, case["id"])["updates"], [])
        with self.assertRaises(engine.APIError):
            engine.choose_decision(self.conn, case["id"], {
                "option_id": "replace:" + SPARE,
                "rationale": "A prohibited car must remain blocked.", "approve": False,
            })
        # Even a caller supplying an explicit confirmation map cannot overwrite failure.
        forced = engine.generate_options(self.conn, case, {
            "replace:" + SPARE: {"country_approval": "Attempted override"},
        })
        self.assertEqual(self.check(self.option({"options": forced}), "country_approval")["status"], "fail")

    def test_target_line_unusable_colour_cannot_be_overridden(self):
        case = self.prepare_case(spare_colour="not usable")
        self.assertEqual(self.check(self.option(case), "source_status")["status"], "fail")
        with self.assertRaises(engine.APIError):
            self.approve(case)

    def test_new_department_information_invalidates_previous_approval(self):
        approved = self.approve(self.confirm_pending(self.prepare_case()))
        updated = engine.record_update(self.conn, approved["id"], {
            "department": "Fictional workshop",
            "message": "New release forecast received; the planner must review the effect.",
        })
        self.assertFalse(updated["decision"]["approved"])
        self.assertEqual(updated["decision"]["option_id"], "replace:" + SPARE)
        self.assertNotEqual(updated["status"], "approved")

    def test_revocation_restores_pending_and_blocks_approval_until_reconfirmed(self):
        approved = self.approve(self.confirm_pending(self.prepare_case()))
        revoked = engine.record_update(self.conn, approved["id"], {
            "department": "Fictional maintenance desk",
            "message": "Technical release withdrawn after a new defect report.",
            "option_id": "replace:" + SPARE,
            "revoke_confirmations": ["technical_release"],
        })
        self.assertFalse(revoked["decision"]["approved"])
        self.assertEqual(self.check(self.option(revoked), "technical_release")["status"], "pending")
        with self.assertRaises(engine.APIError):
            self.approve(revoked)
        reconfirmed = engine.record_update(self.conn, revoked["id"], {
            "department": "Fictional maintenance desk",
            "message": "A new release has now been received after repair.",
            "option_id": "replace:" + SPARE, "confirmations": ["technical_release"],
        })
        self.assertTrue(self.approve(reconfirmed)["decision"]["approved"])

    def test_nominal_capacity_accepts_numeric_product_codes_without_treating_them_as_free_berths(self):
        expected = [("WL33", 4, 33), ("WL30", 4.0, 30), ("BDCM36", 3, 18),
                    ("Bvcmz5", 1, 50), ("Bvcmz6", 1, 60), ("Bimdz", 2, 55)]
        for car_type, code, capacity in expected:
            with self.subTest(car_type=car_type, code=code):
                self.assertEqual(engine.nominal_capacity(car_type, code), capacity)
        self.assertIsNone(engine.nominal_capacity("Bimz", 4))
        case = self.prepare_case()
        self.assertEqual(self.check(self.option(case), "booking_match")["status"], "pending")
        self.assertEqual(self.check(self.option(case, "reduced"), "capacity_recovery")["status"], "pending")

    def test_nominal_formation_capacity_uses_actual_car_codes(self):
        self.add_car(AFFECTED, code="4.0", car_type="WL33")
        self.add_car(SPARE, code="X", car_type="WL33", location="Denderleeuw")
        plan = engine.get_plan(self.conn, "Milan", DAY)
        self.assertEqual(plan["statistics"]["nominal_capacity"]["Comfort Plus"], 33)
        self.assertEqual(plan["statistics"]["commercial_cars"], 1)
        self.assertFalse(plan["formation_order_verified"])

    def test_daily_union_of_exchange_cars_requires_segment_review_not_a_false_hard_failure(self):
        case = self.prepare_case(line="Paris")
        # Twelve fixed cars plus an incoming/outgoing pair do not establish
        # fourteen simultaneous cars on a segment of a thirteen-car route.
        for index in range(11):
            self.add_car(f"88 {index:03}", car_type="Bvcmz6", line="Paris", code="1")
        self.add_car("88 991", car_type="Bvcmz6", line="Paris", code="0")
        self.add_car("88 992", car_type="Bvcmz6", line="Paris", code="-1")
        plan = engine.get_plan(self.conn, "Paris", DAY)
        length_issues = [issue for issue in plan["issues"] if issue["rule"] == "train_length"]
        self.assertTrue(length_issues)
        self.assertTrue(all(issue["severity"] == "warning" for issue in length_issues))
        refreshed = engine.get_case(self.conn, case["id"])
        self.assertEqual(self.check(self.option(refreshed), "formation_length")["status"], "pending")

    def test_fixed_assignments_over_route_limit_remain_a_hard_failure(self):
        case = self.prepare_case(line="Paris")
        for index in range(13):
            self.add_car(f"88 {index:03}", car_type="Bvcmz6", line="Paris", code="1")
        refreshed = engine.get_case(self.conn, case["id"])
        self.assertEqual(self.check(self.option(refreshed), "formation_length")["status"], "fail")
        self.assertEqual(self.option(refreshed)["status"], "blocked")

    def test_decisions_and_department_updates_never_modify_imported_plan(self):
        case = self.prepare_case()
        imported_tables = ("cars", "fleet", "line_days", "running")
        before = {table: self.conn.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall()
                  for table in imported_tables}
        case = self.approve(self.confirm_pending(case))
        engine.record_update(self.conn, case["id"], {
            "department": "Fictional customer service",
            "message": "Additional booking information received; review remains local to the case.",
        })
        after = {table: self.conn.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall()
                 for table in imported_tables}
        self.assertEqual(before, after)

    def test_non_car_disruptions_do_not_require_falsely_marking_a_car_unavailable(self):
        self.add_car(AFFECTED, code="4")
        for case_type in ("capacity_change", "service_delay", "resource_unavailable", "formation_change", "other"):
            with self.subTest(case_type=case_type):
                case = engine.create_case(self.conn, {
                    "case_type": case_type, "line": "Milan", "date": DAY,
                    "reason": "Fictional general service change requiring an operational decision.",
                    "context": {"delay_minutes": 90, "resource": "Shunting crew", "product": "4", "capacity_gap": 20},
                })
                self.assertEqual(case["case_type"], case_type)
                self.assertFalse(case["car_id"])
                self.assertTrue(case["options"])
                self.assertTrue(all(option["kind"] != "replacement" for option in case["options"]))

    def test_workshop_release_case_can_concern_a_catalogued_unassigned_car(self):
        self.add_car(SPARE, code="X", location="Lengerich")
        case = engine.create_case(self.conn, {
            "case_type": "workshop_release", "line": "Milan", "date": DAY,
            "car_id": SPARE, "reason": "Fictional workshop reports a car available for collection.",
        })
        self.assertEqual(case["case_type"], "workshop_release")
        self.assertEqual(case["car_id"], SPARE)
        self.assertTrue(case["options"])
        self.assertFalse(any(option["status"] == "ready" for option in case["options"]))

    def test_custom_planner_proposal_is_review_gated_and_scoped_to_its_case(self):
        self.add_car(AFFECTED, code="4")
        payload = {"case_type": "other", "line": "Milan", "date": DAY,
                   "reason": "Fictional exceptional service arrangement."}
        first = engine.create_case(self.conn, payload)
        second = engine.create_case(self.conn, payload)
        first = engine.create_custom_option(self.conn, first["id"], {
            "title": "Request a revised departure window",
            "description": "Ask the operator to assess a different preparation and departure window.",
            "passenger_impact": "Not yet established", "movement": "Subject to operator assessment",
            "cost_label": "Unconfirmed",
        })
        custom = next(option for option in first["options"] if option["id"].startswith("custom:"))
        self.assertEqual(custom["status"], "conditional")
        self.assertTrue(any(check["status"] == "pending" for check in custom["checks"]))
        self.assertFalse(any(option["id"] == custom["id"] for option in engine.get_case(self.conn, second["id"])["options"]))
        with self.assertRaises(engine.APIError):
            self.approve(first, custom["id"])
        first = self.confirm_pending(first, custom["id"])
        self.assertTrue(self.approve(first, custom["id"])["decision"]["approved"])


if __name__ == "__main__":
    unittest.main()
