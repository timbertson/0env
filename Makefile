0:
	mkzero-gfxmonk -p zeroinstall_env.py -v `cat VERSION` 0path.xml

test:
	0install run http://gfxmonk.net/dist/0install/nosetests-runner.xml --with-doctest --exe -v

.PHONY: 0
