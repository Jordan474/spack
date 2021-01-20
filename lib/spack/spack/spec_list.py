# Copyright 2013-2020 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
import itertools
from six import string_types

import spack.variant
from spack.spec import Spec
from spack.error import SpackError


def spec_ordering_key(s):
    if s.startswith('^'):
        return 5
    elif s.startswith('/'):
        return 4
    elif s.startswith('%'):
        return 3
    elif any(s.startswith(c) for c in '~-+@') or '=' in s:
        return 2
    else:
        return 1


class SpecList(object):

    def __init__(self, name='specs', yaml_list=[], reference={}):
        self.name = name
        self._reference = reference  # TODO: Do we need defensive copy here?

        # Validate yaml_list before assigning
        if not all(isinstance(s, string_types) or isinstance(s, (list, dict))
                   for s in yaml_list):
            raise ValueError(
                "yaml_list can contain only valid YAML types!  Found:\n  %s"
                % [type(s) for s in yaml_list])
        self.yaml_list = yaml_list[:]

        # Expansions can be expensive to compute and difficult to keep updated
        # We cache results and invalidate when self.yaml_list changes
        self._expanded_list = None
        self._constraints = None
        self._specs = None

    @property
    def specs_as_yaml_list(self):
        if self._expanded_list is None:
            self._expanded_list = self._expand_references(self.yaml_list)
        return self._expanded_list

    @property
    def specs_as_constraints(self):
        if self._constraints is None:
            constraints = []
            for item in self.specs_as_yaml_list:
                if isinstance(item, dict):  # matrix of specs
                    constraints.extend(_expand_matrix_constraints(item))
                else:  # individual spec
                    constraints.append([Spec(item)])
            self._constraints = constraints

        return self._constraints

    @property
    def specs(self):
        if self._specs is None:
            specs = []
            # This could be slightly faster done directly from yaml_list,
            # but this way is easier to maintain.
            for constraint_list in self.specs_as_constraints:
                spec = constraint_list[0].copy()
                for const in constraint_list[1:]:
                    spec.constrain(const)
                specs.append(spec)
            self._specs = specs

        return self._specs

    def add(self, spec):
        self.yaml_list.append(str(spec))

        # expanded list can be updated without invalidation
        if self._expanded_list is not None:
            self._expanded_list.append(str(spec))

        # Invalidate cache variables when we change the list
        self._constraints = None
        self._specs = None

    def remove(self, spec):
        # Get spec to remove from list
        remove = [s for s in self.yaml_list
                  if (isinstance(s, string_types) and not s.startswith('$'))
                  and Spec(s) == Spec(spec)]
        if not remove:
            msg = 'Cannot remove %s from SpecList %s\n' % (spec, self.name)
            msg += 'Either %s is not in %s or %s is ' % (spec, self.name, spec)
            msg += 'expanded from a matrix and cannot be removed directly.'
            raise SpecListError(msg)
        assert len(remove) == 1
        self.yaml_list.remove(remove[0])

        # invalidate cache variables when we change the list
        self._expanded_list = None
        self._constraints = None
        self._specs = None

    def extend(self, other, copy_reference=True):
        self.yaml_list.extend(other.yaml_list)
        self._expanded_list = None
        self._constraints = None
        self._specs = None

        if copy_reference:
            self._reference = other._reference

    def update_reference(self, reference):
        self._reference = reference
        self._expanded_list = None
        self._constraints = None
        self._specs = None

    def _parse_reference(self, name):
        sigil = ''
        name = name[1:]

        # Parse specs as constraints
        if name.startswith('^') or name.startswith('%'):
            sigil = name[0]
            name = name[1:]

        # Make sure the reference is valid
        if name not in self._reference:
            msg = 'SpecList %s refers to ' % self.name
            msg += 'named list %s ' % name
            msg += 'which does not appear in its reference dict'
            raise UndefinedReferenceError(msg)

        return (name, sigil)

    def _expand_references(self, yaml):
        if isinstance(yaml, list):
            ret = []
            for item in yaml:
                # if it's a reference, expand it
                if isinstance(item, string_types):
                    if item.startswith('$'):
                        # replace the reference and apply the sigil if needed
                        name, sigil = self._parse_reference(item)
                        referent = [
                            _sigilify(spec, sigil)
                            for spec in self._reference[name].specs_as_yaml_list
                        ]
                        ret.extend(referent)
                    else:
                        ret.append(item)
                else:
                    # else just recurse
                    ret.append(self._expand_references(item))
            return ret
        elif isinstance(yaml, dict):
            # There can't be expansions in dicts
            return dict((name, self._expand_references(val))
                        for (name, val) in yaml.items())
        else:
            # unreachable
            raise ValueError(
                "_expand_references accepts only list or dicts (matrix)"
            )

    def __len__(self):
        return len(self.specs)

    def __getitem__(self, key):
        return self.specs[key]


def _expand_matrix_constraints(object, specify=True):
    # recurse so we can handle nexted matrices

    def _expand_flatten(row):
        """Expand recursively a list and return a flattened list."""
        new_row = []
        for cell in row:
            if isinstance(cell, list):
                new_row.extend(_expand_flatten(cell))
            elif isinstance(cell, dict):
                # Join sub-matrix specs so we wont re-order them later
                new_row.extend([
                    ' '.join(r)
                    for r in _expand_matrix_constraints(cell, specify=False)
                ])
            else:
                new_row.append(cell)
        return new_row

    expanded_rows = []
    for row in object['matrix']:
        if isinstance(row, list):
            # Expand and flatten matrix second dimension
            expanded_rows.append(_expand_flatten(row))
        elif isinstance(row, dict):
            # Matrix in first dimension: expand and concatenate
            expanded_rows.extend([[c] for c in _expand_flatten([row])])
        else:
            # String in first dimension: treat as 1-element row
            expanded_rows.append([row])

    excludes = object.get('exclude', [])  # only compute once
    sigil = object.get('sigil', '')

    results = []
    for combo in itertools.product(*expanded_rows):
        # Construct a combined spec to test against excludes
        ordered_combo = sorted(combo, key=spec_ordering_key)

        test_spec = Spec(' '.join(ordered_combo))
        # Abstract variants don't have normal satisfaction semantics
        # Convert all variants to concrete types.
        # This method is best effort, so all existing variants will be
        # converted before any error is raised.
        # Catch exceptions because we want to be able to operate on
        # abstract specs without needing package information
        try:
            spack.variant.substitute_abstract_variants(test_spec)
        except spack.variant.UnknownVariantError:
            pass
        if any(test_spec.satisfies(x) for x in excludes):
            continue

        if sigil:  # add sigil if necessary
            ordered_combo[0] = sigil + ordered_combo[0]

        # Add to list of constraints
        if specify:
            results.append([Spec(x) for x in ordered_combo])
        else:
            results.append(ordered_combo)
    return results


def _sigilify(item, sigil):
    if isinstance(item, dict):
        if sigil:
            item['sigil'] = sigil
        return item
    else:
        return sigil + item


class SpecListError(SpackError):
    """Error class for all errors related to SpecList objects."""


class UndefinedReferenceError(SpecListError):
    """Error class for undefined references in Spack stacks."""


class InvalidSpecConstraintError(SpecListError):
    """Error class for invalid spec constraints at concretize time."""
