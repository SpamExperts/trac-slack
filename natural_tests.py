import random
import string
import logging
import hashlib
import unittest
import datetime

user = "alex"
random_sel = string.ascii_letters


now = datetime.datetime.utcnow()
today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
yesterday = datetime.datetime.utcnow() - datetime.timedelta(days=1)
yesterday = yesterday.strftime("%Y-%m-%d")
one_week = datetime.datetime.utcnow() - datetime.timedelta(days=7)
one_week = one_week.strftime("%Y-%m-%d")
two_week = datetime.datetime.utcnow() - datetime.timedelta(days=14)
two_week = two_week.strftime("%Y-%m-%d")


TEST_CONFIG = """
[trac]
components = API,Internal Systems,Trac,Pyzor,Logging and Statistics,Update
priorities = lowest,low,normal,high,highest
types = bug,feature,task
extra_fields = points,requests
statuses = assigned_branch_bug,assigned_bug,assigned_feature,assigned_task,assigned_trunk_feature,awaiting_deployment,closed,infoneeded_closed,merge_required,merge_required_branch_bug,needs_information,needs_testing_branch_bug,needs_testing_bug,needs_testing_feature,needs_testing_task,needs_testing_trunk_feature,new,testing_branch_bug,testing_bug,testing_feature,testing_task,testing_trunk_feature,update_documentation,waiting,working_branch_bug,working_bug,working_feature,working_task,working_trunk_feature
[fixed_queries]
moshpit = keywords=moshpit&status=!closed&summary=~metal
last headbang = reporter=%(user)s&milestone=%(month)s %(year)s
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
    # Simple "MY tickets"
    "my tickets":
        u"owner=alex",
    "my bug tickets":
        u"owner=alex&type=bug",
    "my not closed tickets":
        u"owner=alex&status=!closed",
    "bugs assigned to me":
        u"type=bug&owner=alex",
    # Check resolution
    "fixed bugs tickets":
        u"type=bug&resolution=fixed",
    # Component tests
    "Internal Systems not closed bugs":
        u"component=Internal Systems&type=bug&status=!closed",
    # Stars/ends with filters
    "summary starts with tests, owner is alex":
        u"summary=^tests&owner=alex",
    "my assigned features where summary ends with tests":
        u"summary=$tests&owner=alex&status=assigned_feature&type=feature",
    # Higher or normal modifiers
    "my tickets normal or higher":
        u"priority=normal&priority=high&priority=highest&owner=alex",
    "my tickets normal or lower":
        u"priority=lowest&priority=low&priority=normal&owner=alex",
    # Check I'm in partial filter
    "tickets where I'm in cc":
        u"cc=~alex",
    "tickets where I'm not in cc":
        u"cc=!~alex",
    "not closed tickets where I'm in cc":
        u"status=!closed&cc=~alex",
    # Check I have
    "tickets I've reported":
        u"reporter=alex",
    # Check complex status build up
    "my pyzor assigned trunk features":
        u"owner=alex&status=assigned_trunk_feature&type=feature&component"
        u"=Pyzor",
    "tickets that require being merged":
        u"status=merge_required",
    "tickets that require merging":
        u"status=merge_required",
    # Check that the NOT applies to the correct filter
    "not closed bugs high or higher":
        u"status=!closed&type=bug&priority=high&priority=highest",
    "not assigned bugs low or lower":
        u"status=!assigned_bug&type=bug&priority=low&priority=lowest",
    "not closed tickets not in merge required":
        u"status=!closed&status=!merge_required",
    # Check custom fixed queries specified in the config
    "my moshpit":
        u"keywords=moshpit&status=!closed&summary=~metal&owner=alex",
    "not my moshpit":
        u"keywords=!moshpit&status=closed&summary=!~metal&owner=alex",
    "last headbang tickets":
        u"reporter=alex&milestone=%s" % now.strftime("%B %Y"),
    # Check quoting the values
    "description like 'this is a test'":
        u"description=~this is a test",
    "description doesn't contain 'this is a test'":
        u"description=!~this is a test",
    # Time and changetime tests
    "closed bugs from one week ago to yesterday":
        u"status=closed&type=bug&time=%s..%s" % (one_week, yesterday),
    "closed bugs changed since one week ago to yesterday":
        u"status=closed&type=bug&changetime=%s..%s" % (one_week, yesterday),
    "closed bugs from 1 weeks ago to yesterday":
        u"status=closed&type=bug&time=%s..%s" % (one_week, yesterday),
    "closed modified bugs from 1 weeks ago to yesterday":
        u"status=closed&type=bug&changetime=%s..%s" % (one_week, yesterday),
    "my new features from 2 weeks ago":
        u"status=new&type=feature&owner=alex&time=%s..%s" % (two_week, today),
    "my new features changed since 2 weeks ago":
        u"status=new&type=feature&owner=alex&changetime=%s..%s" % (two_week,
                                                                   today),
    "my bug tickets since 2016-01-01":
        u"type=bug&owner=alex&time=2016-01-01..%s" % today,
    "my bug tickets modified since 2016-01-01":
        u"type=bug&owner=alex&changetime=2016-01-01..%s" % today,
    "feature tickets I've reported from last week":
        u"reporter=alex&type=feature&time=%s..%s" % (one_week, today),
    "feature tickets I've reported changed last week":
        u"reporter=alex&type=feature&changetime=%s..%s" % (one_week, today),
    "bug tickets since July 24th":
        u"type=bug&time=2016-07-24..%s" % today,
    "bug tickets changed since July 24th":
        u"type=bug&changetime=2016-07-24..%s" % today,
    "tickets since 2016/07/04 and before the 24th of August":
        u"time=2016-07-04..2016-08-24",
    "tickets changed after 2016/07/04 and before the 24th of August":
        u"changetime=2016-07-04..2016-08-24",
    "bug tickets after the 26th of july but before the 27th of August":
        u"type=bug&time=2016-07-26..2016-08-27",
    "bug tickets changed after the 26th of july but before the 27th of August":
        u"type=bug&changetime=2016-07-26..2016-08-27",
    "task tickets opened in the last two weeks":
        u"type=task&time=%s..%s" % (two_week, today),
    "normal feature tickets changed in the last two weeks":
        u"type=feature&priority=normal&changetime=%s..%s" % (two_week, today),
    # Failing:
    # u"not update_documentation":
    #     u"status=!update_documentation",
}

for l, e in CASES.items():
    case = create_test(l, e)
    case_name = "test_" + hashlib.md5(l).hexdigest()
    setattr(NaturalTest, case_name, case)
    l = "show " + l
    case = create_test(l, e)
    case_name = "test_" + hashlib.md5(l).hexdigest()
    setattr(NaturalTest, case_name, case)


