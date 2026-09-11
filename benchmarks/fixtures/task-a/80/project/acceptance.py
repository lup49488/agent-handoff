from slug import slugify


def check(actual, expected):
    if actual != expected:
        raise AssertionError(repr(actual) + " != " + repr(expected))


check(slugify(" Café & Tea "), "cafe-and-tea")
check(slugify("C# Basics"), "c-sharp-basics")
check(slugify("many---spaces"), "many-spaces")
try:
    slugify(3)
except TypeError:
    pass
else:
    raise AssertionError("non-string input must raise TypeError")
