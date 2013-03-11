#!/usr/bin/python

## Binary Analysis Tool
## Copyright 2013 Armijn Hemel for Tjaldur Software Governance Solutions
## Licensed under Apache 2.0, see LICENSE file for details

'''
This plugin is used to generate reports. It is run as an aggregate scan for
a reason: as it turns out many reports that are generated are identical:
matched strings are often the same since the same database is used.

Since generating reports can have quite a bit of overhead it makes sense
to first deduplicate and then generate reports.

The method works as follows:

1. All data from pickles that is needed to generate reports is extracted in
parallel.
2. The checksums of the pickles are computed and recorded. If there is a
duplicate the duplicate pickle is removed and it is recorded which file it
originally belonged to.
3. Reports are (partially) generated in parallel for the remaining pickle files.
4. The reports are copied and renamed, or assembled from partial reports
'''

import os, os.path, sys, copy, cPickle, tempfile, hashlib, shutil, multiprocessing, cgi, gzip

## compute a SHA256 hash. This is done in chunks to prevent a big file from
## being read in its entirety at once, slowing down a machine.
def gethash(path):
	scanfile = open(path, 'r')
	h = hashlib.new('sha256')
	scanfile.seek(0)
	hashdata = scanfile.read(10000000)
	while hashdata != '':
		h.update(hashdata)
		hashdata = scanfile.read(10000000)
	scanfile.close()
	return h.hexdigest()

## helper function to condense version numbers and squash numbers.
def squash_versions(versions):
	if len(versions) <= 3:
		versionline = reduce(lambda x, y: x + ", " + y, versions)
		return versionline
	# check if we have versions without '.'
	if len(filter(lambda x: '.' not in x, versions)) != 0:
		versionline = reduce(lambda x, y: x + ", " + y, versions)
		return versionline
	versionparts = []
	# get the major version number first
	majorv = list(set(map(lambda x: x.split('.')[0], versions)))
	for m in majorv:
		maxconsolidationlevel = 0
		## determine how many subcomponents we have at max
		filterversions = filter(lambda x: x.startswith(m + "."), versions)
		if len(filterversions) == 1:
			versionparts.append(reduce(lambda x, y: x + ", " + y, filterversions))
			continue
		minversionsplits = min(list(set(map(lambda x: len(x.split('.')), filterversions)))) - 1
		## split with a maximum of minversionsplits splits
		splits = map(lambda x: x.split('.', minversionsplits), filterversions)
		for c in range(0, minversionsplits):
			if len(list(set(map(lambda x: x[c], splits)))) == 1:
				maxconsolidationlevel = maxconsolidationlevel + 1
			else: break
		if minversionsplits != maxconsolidationlevel:
			splits = map(lambda x: x.split('.', maxconsolidationlevel), filterversions)
		versionpart = reduce(lambda x, y: x + "." + y, splits[0][:maxconsolidationlevel]) + ".{" + reduce(lambda x, y: x + ", " + y, map(lambda x: x[-1], splits)) + "}"
		versionparts.append(versionpart)
	versionline = reduce(lambda x, y: x + ", " + y, versionparts)
	return versionline

def generatehtmlsnippet((picklefile, pickledir, picklehash, reportdir)):
	html_pickle = open(os.path.join(pickledir, picklefile), 'rb')
	(packagename, uniquematches) = cPickle.load(html_pickle)
        html_pickle.close()
	if len(uniquematches) == 0:
		return

	uniquehtml = "<hr><h2><a name=\"%s\" href=\"#%s\">Matches for: %s (%d)</a></h2>" % (packagename, packagename, packagename, len(uniquematches))
	for k in uniquematches:
		(programstring, results) = k
		## we have a list of tuples, per unique string we have a list of sha256sums and meta info
		## This is really hairy
		if len(results) > 0:
			uniquehtml = uniquehtml + "<h5>%s</h5><p><table><tr><td><b>Filename</b></td><td><b>Version(s)</b></td><td><b>Line number</b></td><td><b>SHA256</b></td></tr>" % cgi.escape(programstring)
			uniqtablerows = []
			sh = {}
			for s in results:
				(checksum, version, linenumber, sourcefile) = s
				## if possible, remove the package name, plus version number, from the path
				## that is displayed. This is to prevent that a line is printed for every
				## version, even when the code has not changed. Usually it will be clear
				## which file is meant.
				(pv, fp) = sourcefile.split('/', 1)
				## clean up some names first, especially when they have been changed by Debian
				for e in ["+dfsg", "~dfsg", ".orig", ".dfsg1", ".dfsg2"]:
					if pv.endswith(e):
						pv = pv[:-len(e)]
						break
				if pv == "%s-%s" % (packagename, version) or pv == "%s_%s" % (packagename, version):
					if sh.has_key(checksum):
						sh[checksum].append((fp, version, linenumber))
					else:
						sh[checksum] = [(fp, version, linenumber)]
				else:
					if sh.has_key(checksum):
						sh[checksum].append((sourcefile, version, linenumber))
					else:
						sh[checksum] = [(sourcefile, version, linenumber)]

			for checksum in sh:
				## per checksum we have a list of (filename, version)
				## Now we need to check if we only have one filename, or if there are multiple.
				## If there is just one it is easy:
				if len(set(map(lambda x: x[0], sh[checksum]))) == 1:
					lines = sorted(set(map(lambda x: (x[2]), sh[checksum])))
					versions = sorted(set(map(lambda x: (x[1]), sh[checksum])))
					versionline = squash_versions(versions)
					numlines = reduce(lambda x, y: x + ", " + y, map(lambda x: "<a href=\"unique:/%s#%d\">%d</a>" % (checksum, x, x), lines))
					uniqtablerows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>\n" % (sh[checksum][0][0], versionline, numlines, checksum))
				else:   
					for d in list(set(map(lambda x: x[0], sh[checksum]))):
						filterd = filter(lambda x: x[0] == d, sh[checksum])
						lines = sorted(set(map(lambda x: (x[2]), filterd)))
						versions = sorted(set(map(lambda x: (x[1]), filterd)))
						versionline = squash_versions(versions)
						numlines = reduce(lambda x, y: x + ", " + y, map(lambda x: "<a href=\"unique:/%s#%d\">%d</a>" % (checksum, x, x), lines))
						uniqtablerows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>\n" % (d, versionline, numlines, checksum))
			uniquehtml = uniquehtml + reduce(lambda x, y: x + y, uniqtablerows, "") + "</table></p>\n"
		else:
			uniquehtml = uniquehtml + "<h5>%s</h5>" % cgi.escape(programstring)
	uniquehtmlfile = open("%s/%s-unique.snippet" % (reportdir, picklehash), 'wb')
	uniquehtmlfile.write(uniquehtml)
	uniquehtmlfile.close()
	os.unlink(os.path.join(pickledir, picklefile))

## a bit of a misnomer, since this method also generates a few things
def extractpickles((filehash, pickledir, topleveldir, reportdir)):
	leaf_file = open(os.path.join(topleveldir, "filereports", "%s-filereport.pickle" % filehash), 'rb')
	leafreports = cPickle.load(leaf_file)
	leaf_file.close()
	## return type: (filehash, reportresults, unmatchedresult)
	reportresults = []

	## (picklehash, picklename)
	unmatchedresult = None
	if not leafreports.has_key('ranking'):
		return (filehash, reportresults, unmatchedresult)
	## the ranking result is (res, dynamicRes, variablepvs)
	(res, dynamicRes, variablepvs) = leafreports['ranking']

	if res['unmatched'] != []:
		unmatches = list(set(res['unmatched']))
		unmatches.sort()

		tmppickle = tempfile.mkstemp()

		cPickle.dump(unmatches, os.fdopen(tmppickle[0], 'w'))
		picklehash = gethash(tmppickle[1])
		unmatchedresult = (picklehash, tmppickle[1])

	if res['reports'] != []:
		for j in res['reports']:
			(rank, packagename, uniquematches, percentage, packageversions, licenses) = j
			if len(uniquematches) == 0:
				continue
			tmppickle = tempfile.mkstemp()
			cPickle.dump((packagename, uniquematches), os.fdopen(tmppickle[0], 'w'))
			picklehash = gethash(tmppickle[1])
			reportresults.append((rank, picklehash, tmppickle[1], len(uniquematches), packagename))

	if dynamicRes != {}:
		header = "<html><body>"
		html = ""
		if dynamicRes.has_key('uniquepackages'):
			if dynamicRes['uniquepackages'] != {}:
				html += "<h1>Unique function name matches per package</h1><p><ul>\n"
				ukeys = map(lambda x: (x[0], len(x[1])), dynamicRes['uniquepackages'].items())
				ukeys.sort(key=lambda x: x[1], reverse=True)
				for i in ukeys:
					html += "<li><a href=\"#%s\">%s (%d)</a>" % (i[0], i[0], i[1])
				html += "</ul></p>"
				for i in ukeys:
					html += "<hr><h2><a name=\"%s\" href=\"#%s\">Matches for %s (%d)</a></h2><p>\n" % (i[0], i[0], i[0], i[1])
					upkgs = dynamicRes['uniquepackages'][i[0]]
					upkgs.sort()
					for v in upkgs:
						html += "%s<br>\n" % v
					html += "</p>\n"
		footer = "</body></html>"
		if html != "":
			html = header + html + footer
			nameshtmlfile = gzip.open("%s/%s-functionnames.html.gz" % (reportdir, filehash), 'wb')
			nameshtmlfile.write(html)
			nameshtmlfile.close()

	if variablepvs != {}:
		header = "<html><body>"
		html = ""
		language = variablepvs['language']

		if language == 'Java':
			fieldspackages = {}
			sourcespackages = {}
			classespackages = {}
			fieldscount = {}
			sourcescount = {}
			classescount = {}
			for i in ['classes', 'sources', 'fields']:
				if not variablepvs.has_key(i):
					continue
				packages = {}
				packagecount = {}
				if variablepvs[i] != []:
					for c in variablepvs[i]:
						lenres = len(list(set(map(lambda x: x[0], variablepvs[i][c]))))
						if lenres == 1:
							pvs = variablepvs[i][c]
							(package,version) = variablepvs[i][c][0]
							if packagecount.has_key(package):
								packagecount[package] = packagecount[package] + 1
							else:
								packagecount[package] = 1
							'''
							## for later use
							for p in pvs:
								(package,version) = p
								if packages.has_key(package):
									packages[package].append(version)
								else:
									packages[package] = [version]
							'''
				if packagecount != {}:
					if i == 'classes':
						classescount = packagecount
					if i == 'sources':
						sourcescount = packagecount
					if i == 'fields':
						fieldscount = packagecount

				if packages != {}:
					if i == 'classes':
						classespackages = packages
					if i == 'sources':
						sourcespackages = packages
					if i == 'fields':
						fieldspackages = packages

			if classescount != {}:
				html = html + "<h3>Unique matches of class names</h3>\n<table>\n"
				html = html + "<tr><td><b>Name</b></td><td><b>Unique matches</b></td></tr>"
				for i in classescount:
					html = html + "<tr><td>%s</td><td>%d</td></tr>\n" % (i, classescount[i])
				html = html + "</table>\n"

			if sourcescount != {}:
				html = html + "<h3>Unique matches of source file names</h3>\n<table>\n"
				html = html + "<tr><td><b>Name</b></td><td><b>Unique matches</b></td></tr>"
				for i in sourcescount:
					html = html + "<tr><td>%s</td><td>%d</td></tr>\n" % (i, sourcescount[i])
				html = html + "</table>\n"

			if fieldscount != {}:
				html = html + "<h3>Unique matches of field names</h3>\n<table>\n"
				html = html + "<tr><td><b>Name</b></td><td><b>Unique matches</b></td></tr>"
				for i in fieldscount:
					html = html + "<tr><td>%s</td><td>%d</td></tr>\n" % (i, fieldscount[i])
				html = html + "</table>\n"

		if language == 'C':
			if variablepvs.has_key('variables'):
				packages = {}
				packagecount = {}
				## for each variable name determine in how many packages it can be found.
				## Only the unique packages are reported.
				for c in variablepvs['variables']:
					lenres = len(variablepvs['variables'][c])
					if lenres == 1:
						pvs = variablepvs['variables'][c]
						package = variablepvs['variables'][c].keys()[0]
						if packagecount.has_key(package):
							packagecount[package] = packagecount[package] + 1
						else:
							packagecount[package] = 1
							
					'''
					## for later use
					for p in pvs:
						(package,version) = p
						if packages.has_key(package):
							packages[package].append(version)
						else:
							packages[package] = [version]
					'''

				if packagecount != {}:
					html = html + "<h3>Unique matches of variables</h3>\n<table>\n"
					html = html + "<tr><td><b>Name</b></td><td><b>Unique matches</b></td></tr>"
					for i in packagecount:
						html = html + "<tr><td>%s</td><td>%d</td></tr>\n" % (i, packagecount[i])
					html = html + "</table>\n"

		footer = "</body></html>"
		if html != "":
			html = header + html + footer
			nameshtmlfile = gzip.open("%s/%s-names.html.gz" % (reportdir, filehash), 'wb')
			nameshtmlfile.write(html)
			nameshtmlfile.close()

	return (filehash, reportresults, unmatchedresult)

def generateunmatched((picklefile, pickledir, filehash, reportdir)):

	unmatched_pickle = open(os.path.join(pickledir, picklefile), 'rb')
	unmatches = cPickle.load(unmatched_pickle)
        unmatched_pickle.close()

	unmatchedhtml = "<html><body><h1>Unmatched strings</h1><p><ul>"
	for i in unmatches:
		unmatchedhtml = unmatchedhtml + "%s<br>\n" % cgi.escape(i)
	unmatchedhtml = unmatchedhtml + "</body></html>"
	unmatchedhtmlfile = gzip.open("%s/%s-unmatched.html.gz" % (reportdir, filehash), 'wb')
	unmatchedhtmlfile.write(unmatchedhtml)
	unmatchedhtmlfile.close()
	os.unlink(os.path.join(pickledir, picklefile))

def generatereports(unpackreports, scantempdir, topleveldir, envvars=None):
	scanenv = os.environ.copy()
	if envvars != None:
		for en in envvars.split(':'):
			try:
				(envname, envvalue) = en.split('=')
				scanenv[envname] = envvalue
			except Exception, e:
				pass

	reportdir = scanenv.get('BAT_REPORTDIR', "%s/%s" % (topleveldir, "reports"))
	try:
		os.stat(reportdir)
	except:
		## BAT_IMAGEDIR does not exist
		try:
			os.makedirs(reportdir)
		except Exception, e:
			return

	pickledir = scanenv.get('BAT_PICKLEDIR', "%s/%s" % (topleveldir, "pickles"))
	try:
		os.stat(pickledir)
	except:
		## BAT_PICKLEDIR does not exist
		try:
			os.makedirs(pickledir)
		except Exception, e:
			return

	rankingfiles = []

	## filter out the files which don't have ranking results
	for i in unpackreports:
		if not unpackreports[i].has_key('sha256'):
			continue
		if not unpackreports[i].has_key('tags'):
			continue
		if not 'ranking' in unpackreports[i]['tags']:
			continue
		filehash = unpackreports[i]['sha256']
		if not os.path.exists(os.path.join(topleveldir, "filereports", "%s-filereport.pickle" % filehash)):
			continue
		rankingfiles.append(i)

	pickles = []
	processed = []
	unmatchedpicklespackages = []
	picklespackages = []
	picklehashes = {}
	pickletofile = {}
	unmatchedpickles = []
	reportpickles = []

	filehashes = list(set(map(lambda x: unpackreports[x]['sha256'], rankingfiles)))

	## extract pickles
	extracttasks = map(lambda x: (x, pickledir, topleveldir, reportdir), filehashes)
	pool = multiprocessing.Pool()
	res = filter(lambda x: x != None, pool.map(extractpickles, extracttasks))
	pool.terminate()

	## {filehash: [(rank, picklehash)]}
	resultranks = {}

	bla = 0
	for r in res:
		(filehash, resultreports, unmatchedresult) = r
		if r == None:
			continue
		if unmatchedresult != None:
			(picklehash, tmppickle) = unmatchedresult
			if picklehash in unmatchedpickles:
				if pickletofile.has_key(picklehash):
					pickletofile[picklehash].append(filehash)
				else:
					pickletofile[picklehash] = [filehash]
				unmatchedpicklespackages.append((picklehash, filehash))
				os.unlink(tmppickle)
			else:
				shutil.move(tmppickle, pickledir)
				unmatchedpickles.append(picklehash)
				unmatchedpicklespackages.append((picklehash, filehash))
				picklehashes[picklehash] = os.path.basename(tmppickle)
				if pickletofile.has_key(picklehash):
					pickletofile[picklehash].append(filehash)
				else:
					pickletofile[picklehash] = [filehash]
		if resultreports != []:
			bla += 1
			for report in resultreports:
				(rank, picklehash, tmppickle, uniquematcheslen, packagename) = report
				if resultranks.has_key(filehash):
					resultranks[filehash].append((rank, picklehash, uniquematcheslen, packagename))
				else:
					resultranks[filehash] = [(rank, picklehash, uniquematcheslen, packagename)]
				if picklehash in reportpickles:
					if pickletofile.has_key(picklehash):
						pickletofile[picklehash].append(filehash)
					else:
						pickletofile[picklehash] = [filehash]
					picklespackages.append((picklehash, filehash))
					os.unlink(tmppickle)
				else:
					shutil.move(tmppickle, pickledir)
					reportpickles.append(picklehash)
					picklespackages.append((picklehash, filehash))
					picklehashes[picklehash] = os.path.basename(tmppickle)
					if pickletofile.has_key(picklehash):
						pickletofile[picklehash].append(filehash)
					else:
						pickletofile[picklehash] = [filehash]

	pool = multiprocessing.Pool()

	## generate files for unmatched strings
	if unmatchedpickles != []:
		unmatchedtasks = list(set(map(lambda x: (picklehashes[x[0]], pickledir, x[0], reportdir), unmatchedpicklespackages)))
		results = pool.map(generateunmatched, unmatchedtasks, 1)
		for p in unmatchedpicklespackages:
			oldfilename = "%s-%s" % (p[0], "unmatched.html.gz")
			filename = "%s-%s" % (p[1], "unmatched.html.gz")
			if os.path.exists(os.path.join(reportdir, oldfilename)):
				shutil.copy(os.path.join(reportdir, oldfilename), os.path.join(reportdir, filename))
		for p in unmatchedpicklespackages:
			try:
				filename = "%s-%s" % (p[0], "unmatched.html.gz")
				os.unlink(os.path.join(reportdir, filename))
			except Exception, e:
				#print >>sys.stderr, "ERR", e
				pass
	if reportpickles != []:
		reporttasks = list(set(map(lambda x: (picklehashes[x[0]], pickledir, x[0], reportdir), picklespackages)))
		pool.map(generatehtmlsnippet, reporttasks, 1)
		## now recombine the results into HTML files
		pickleremoves = []
		for filehash in resultranks.keys():
			uniquehtml = "<html><body><h1>Unique matches per package</h1><p><ul>"
			headers = ""
			filehtml = ""
			for r in resultranks[filehash]:
				(rank, picklehash, uniquematcheslen, packagename) = r
				headers = headers + "<li><a href=\"#%s\">%s (%d)</a>" % (packagename, packagename, uniquematcheslen)
				picklehtmlfile = open(os.path.join(reportdir, "%s-unique.snippet" % picklehash))
				picklehtml = picklehtmlfile.read()
				picklehtmlfile.close()
				filehtml = filehtml + picklehtml
				pickleremoves.append(picklehash)
				
			uniquehtml = uniquehtml + headers + "</ul></p>" + filehtml + "</body></html>"
			uniquehtmlfile = gzip.open("%s/%s-unique.html.gz" % (reportdir, filehash), 'wb')
			uniquehtmlfile.write(uniquehtml)
			uniquehtmlfile.close()
		for i in list(set(pickleremoves)):
			try:
				os.unlink(os.path.join(reportdir, "%s-unique.snippet" % i))
			except Exception, e:
				## print >>sys.stderr, e
				pass
	pool.terminate()