import random
import string
import logging
import hashlib
import unittest

user = "alex"
random_sel = string.ascii_letters



TEST_CONFIG = """
[trac]
components = API,Internal Systems,Trac,Pyzor,Logging and Statistics,Update
priorities = lowest,low,normal,high,highest
types = bug,feature,task
extra_fields = points,requests
statuses = assigned_branch_bug,assigned_bug,assigned_feature,assigned_task,assigned_trunk_feature,awaiting_deployment,closed,infoneeded_closed,merge_required,merge_required_branch_bug,needs_information,needs_testing_branch_bug,needs_testing_bug,needs_testing_feature,needs_testing_task,needs_testing_trunk_feature,new,testing_branch_bug,testing_bug,testing_feature,testing_task,testing_trunk_feature,update_documentation,waiting,working_branch_bug,working_bug,working_feature,working_task,working_trunk_feature
[fixed_queries]
moshpit = keywords=moshpit&status=!closed&summary=~metal
"""

original_config = open("/etc/trac-slack.conf").read()

class NaturalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open("/etc/trac-slack.conf", "w") as tsf:
            tsf.write(TEST_CONFIG)

        logger = logging.getLogger("trac-slack-nlp")
        logger.setLevel(logging.DEBUG)
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG)
        logger.addHandler(sh)

    @classmethod
    def tearDownClass(cls):
        with open("/etc/trac-slack.conf", "w") as tsf:
            tsf.write(original_config)


def create_test(language, expected):
    expected = sorted(expected.split("&"))

    def test_case(self):
        import natural
        result = natural.natural_to_query(language, user)
        result = sorted(result.split("&"))
        self.assertEqual(result, expected,
                         (language, result, expected))
    return test_case


CASES = {
    "my tickets":
        u"owner=alex",
    "my bug tickets":
        u"owner=alex&type=bug",
    "my not closed tickets":
        u"owner=alex&status=!closed",
    "Internal Systems not closed bugs":
        u"component=Internal Systems&type=bug&status=!closed",
    "summary starts with tests, owner is alex":
        u"summary=^tests&owner=alex",
    "my assigned features where summary ends with tests":
        u"summary=$tests&owner=alex&status=assigned_feature&type=feature",
    "my tickets normal of higher":
        u"priority=normal&priority=high&priority=highest&owner=alex",
    "my tickets normal of lower":
        u"priority=lowest&priority=low&priority=normal&owner=alex",
    "tickets where I'm in cc":
        u"cc=alex",
    "tickets where I'm not in cc":
        u"cc=!alex",
    "not closed tickets where I'm in cc":
        u"status=!closed&cc=alex",
    "my pyzor assigned trunk features":
        u"owner=alex&status=assigned_trunk_feature&type=feature&component"
        u"=Pyzor",
    "not closed bugs high or higher":
        u"status=!closed&type=bug&priority=high&priority=highest",
    "not assigned bugs low or lower":
        u"status=!assigned_bug&type=bug&priority=low&priority=lowest",
    "my moshpit":
        u"keywords=moshpit&status=!closed&summary=~metal&owner=alex",
    "not my moshpit":
        u"keywords=!moshpit&status=closed&summary=!~metal&owner=alex",
    # Failing:
    u"not update_documentation":
        u"status=!update_documentation",
}

for l, e in CASES.items():
    case = create_test(l, e)
    case_name = "test_" + hashlib.md5(l).hexdigest()
    setattr(NaturalTest, case_name, case)

