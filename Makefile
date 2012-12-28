0:
	mkzero-gfxmonk -p zeroinstall_env.py -v `cat VERSION` 0env.xml

0env-local.xml: 0env.xml
	0install run http://gfxmonk.net/dist/0install/0local.xml 0env.xml

test: 0env-local.xml
	0install run --command=test 0env-local.xml

testall: 0env-local.xml
	# test on python versions (2.6, 2.7, 3.x)
	0install run http://0install.net/2008/interfaces/0test.xml 0env-local.xml http://repo.roscidus.com/python/python 2.6,2.7 2.7,2.8 3,4


.PHONY: 0 test testall
