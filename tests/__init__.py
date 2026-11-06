# Makes tests.support importable as a dotted path (the file keyring
# backend, tests.support.filekeyring.FileKeyring) in any environment.
# Without this package marker, "tests" resolves as a namespace package
# and loses to any unrelated regular "tests" package installed on the
# machine, which silently breaks every suite that loads the keyring
# backend by name.
