"""Common code for models"""

from django.db import models

from types import MethodType

# This class taken from:
# http://lazypython.blogspot.com/2009/01/building-magic-manager.html
# and modifed by me to actually work
class ChainableManager(models.Manager):
    """Allows chaining of Manager methods"""
    # pylint: disable-msg=R0904

    def get_query_set(self):
        """Dynamically adds custom methods to returned QuerySet"""
        print 'In outer gqs'
        qset = super(ChainableManager, self).get_query_set()

        class _QuerySet(qset.__class__):
            """Dynamic class"""
            # pylint: disable-msg=R0903
            pass

        for method in [attr for attr in dir(self) if not attr.startswith('__') and
                                                     type(getattr(self, attr)) == MethodType and
                                                     not hasattr(_QuerySet, attr)]:
            setattr(_QuerySet, method, MethodType(getattr(self, method).im_func, None, _QuerySet))
        qset.__class__ = _QuerySet
        print 'gqs\t', id(qset), qset.__class__
        return qset
