"""
audfprint_match.py

Fingerprint matching code for audfprint

2014-05-26 Dan Ellis dpwe@ee.columbia.edu
"""
import librosa
import numpy as np
import scipy.signal

import time
# for checking phys mem size
import resource
# for localtest and illustrate
import audfprint_analyze
import matplotlib.pyplot as plt

from scipy import stats

def log(message):
    """ log info with stats """
    print time.ctime(), \
        "physmem=", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, \
        "utime=", resource.getrusage(resource.RUSAGE_SELF).ru_utime, \
        message

def encpowerof2(val):
    """ Return N s.t. 2^N >= val """
    return int(np.ceil(np.log(max(1, val))/np.log(2)))

def locmax(vec, indices=False):
    """ Return a boolean vector of which points in vec are local maxima.
        End points are peaks if larger than single neighbors.
        if indices=True, return the indices of the True values instead
        of the boolean vector. (originally from audfprint.py)
    """
    # x[-1]-1 means last value can be a peak
    #nbr = np.greater_equal(np.r_[x, x[-1]-1], np.r_[x[0], x])
    # the np.r_ was killing us, so try an optimization...
    nbr = np.zeros(len(vec)+1, dtype=bool)
    nbr[0] = True
    nbr[1:-1] = np.greater_equal(vec[1:], vec[:-1])
    maxmask = (nbr[:-1] & ~nbr[1:])
    if indices:
        return np.nonzero(maxmask)[0]
    else:
        return maxmask

def find_modes(data, threshold=5, window=0):
    """ Find multiple modes in data,  Report a list of (mode, count)
        pairs for every mode greater than or equal to threshold.
        Only local maxima in counts are returned.
    """
    # TODO: Ignores window at present
    datamin = np.amin(data)
    fullvector = np.bincount(data - datamin)
    # Find local maxima
    localmaxes = np.nonzero(np.logical_and(locmax(fullvector),
                                           np.greater_equal(fullvector,
                                                            threshold)))[0]
    return localmaxes + datamin, fullvector[localmaxes]

class Matcher(object):
    """Provide matching for audfprint fingerprint queries to hash table"""

    def __init__(self):
        """Set up default object values"""
        # Tolerance window for time differences
        self.window = 1
        # Absolute minimum number of matching hashes to count as a match
        self.threshcount = 5
        # How many hits to return?
        self.max_returns = 1
        # How deep to search in return list?
        self.search_depth = 100
        # Sort those returns by time (instead of counts)?
        self.sort_by_time = False
        # Verbose reporting?
        self.verbose = False
        # Do illustration?
        self.illustrate = False
        # Careful counts?
        self.exact_count = False

    def _best_count_ids(self, hits, ht):
        """ Return the indexes for the ids with the best counts.
            hits is a matrix as returned by hash_table.get_hits()
            with rows of consisting of [id dtime hash otime] """
        allids = hits[:, 0]
        ids = np.unique(allids)
        #rawcounts = np.sum(np.equal.outer(ids, allids), axis=1)
        # much faster, and doesn't explode memory
        rawcounts = np.bincount(allids)[ids]
        # Divide the raw counts by the total number of hashes stored
        # for the ref track, to downweight large numbers of chance
        # matches against longer reference tracks.
        wtdcounts = rawcounts/(ht.hashesperid[ids].astype(float))

        # Find all the actual hits for a the most popular ids
        bestcountsixs = np.argsort(wtdcounts)[::-1]
        # We will examine however many hits have rawcounts above threshold
        # up to a maximum of search_depth.
        maxdepth = np.minimum(np.count_nonzero(np.greater(rawcounts,
                                                          self.threshcount)),
                              self.search_depth)
        # Return the ids to check
        bestcountsixs = bestcountsixs[:maxdepth]
        return ids[bestcountsixs], rawcounts[bestcountsixs]

    def _unique_match_hashes(self, id, hits, mode):
        """ Return the list of unique matching hashes.  Split out so
            we can recover the actual matching hashes for the best
            match if required. """
        allids = hits[:, 0]
        alltimes = hits[:, 1]
        allhashes = hits[:, 2].astype(np.int64)
        allotimes = hits[:, 3]
        timebits = max(1, encpowerof2(np.amax(allotimes)))
        # matchhashes may include repeats because multiple
        # ref hashes may match a single query hash under window.
        # Uniqify:
        #matchhashes = sorted(list(set(matchhashes)))
        # much, much faster:
        matchix = np.nonzero(
            np.logical_and(allids == id, np.less_equal(np.abs(alltimes - mode),
                                                       self.window)))[0]
        matchhasheshash = np.unique(allotimes[matchix]
                                    + (allhashes[matchix] << timebits))
        timemask = (1 << timebits) - 1
        matchhashes = np.c_[matchhasheshash & timemask,
                            matchhasheshash >> timebits]
        return matchhashes

    def _exact_match_counts(self, hits, ids, rawcounts, hashesfor=None):
        """ Find the number of "filtered" (time-consistent) matching
            hashes for each of the promising ids in <ids>.  Return an
            np.array whose rows are [id, filtered_count, modal_time_skew,
            unfiltered_count, original_rank].  Results are sorted by
            original rank (but will not in general include all the the
            original IDs).  There can be multiple rows for a single
            ID, if there are several distinct time_skews giving good
            matches. """
        # Slower, old process for exact match counts
        allids = hits[:, 0]
        alltimes = hits[:, 1]
        allhashes = hits[:, 2]
        allotimes = hits[:, 3]
        maxotime = np.amax(allotimes)
        # Allocate enough space initially for 4 modes per hit
        maxnresults = len(ids) * 4
        results = np.zeros((maxnresults, 5), np.int32)
        nresults = 0
        for urank, id, rawcount in zip(range(len(ids)), ids, rawcounts):
            modes, counts = find_modes(alltimes[np.nonzero(allids==id)[0]],
                                       window=self.window,
                                       threshold=self.threshcount)
            for mode in modes:
                matchhashes = self._unique_match_hashes(id, hits, mode)
                # Now we get the exact count
                filtcount = len(matchhashes)
                if filtcount >= self.threshcount:
                    if nresults == maxnresults:
                        # Extend array
                        maxnresults *= 2
                        results.resize((maxnresults, 5))
                    results[nresults, :] = [id, filtcount, mode, rawcount,
                                            urank]
                    nresults += 1
        return results[:nresults, :]

    def _approx_match_counts(self, hits, ids, rawcounts):
        """ Quick and slightly inaccurate routine to find the
            number of time-aligned hits from the raw iist of hits.
            Only considers largest mode for reference ID match.
            Returns rows [id, filt_count, time_skew, raw_count, orig_rank] """
        # In fact, the counts should be the same as exact_match_counts
        # *but* some matches may be pruned because we don't bother to
        # apply the window (allowable drift in time alignment) unless
        # there are more than threshcount matches at the single best time skew.
        results = np.zeros((len(ids), 5), np.int32)
        if not hits.size:
            # No hits found, return empty results
            return results
        allids = hits[:, 0]
        alltimes = hits[:, 1]
        # Make sure every value in alltimes is >=0 for bincount
        mintime = np.amin(alltimes)
        alltimes -= mintime
        nresults = 0
        # Hash IDs and times together, so only a single bincount
        timebits = max(1, encpowerof2(np.amax(alltimes)))
        allbincounts = np.bincount((allids << timebits) + alltimes)
        for urank, id, rawcount in zip(range(len(ids)), ids, rawcounts):
            # Select the subrange of bincounts corresponding to this id
            bincounts = allbincounts[(id << timebits):(((id+1)<<timebits)-1)]
            mode = np.argmax(bincounts)
            if bincounts[mode] <= self.threshcount:
                # Too few - skip to the next id
                continue
            count = np.sum(bincounts[max(0, mode-self.window) :
                                     (mode+self.window+1)])
            results[nresults, :] = [id, count, mode+mintime, rawcount, urank]
            nresults += 1
        return results[:nresults, :]

    def match_hashes(self, ht, hashes, hashesfor=None):
        """ Match audio against fingerprint hash table.
            Return top N matches as (id, filteredmatches, timoffs, rawmatches)
            If hashesfor specified, return the actual matching hashes for that
            hit (0=top hit).
        """
        # find the implicated id, time pairs from hash table
        #log("nhashes=%d" % np.shape(hashes)[0])
        hits = ht.get_hits(hashes)

        bestids, rawcounts = self._best_count_ids(hits, ht)

        #log("len(rawcounts)=%d max(bestcountsixs)=%d" %
        #    (len(rawcounts), max(bestcountsixs)))
        if not self.exact_count:
            results = self._approx_match_counts(hits, bestids, rawcounts)
        else:
            results = self._exact_match_counts(hits, bestids, rawcounts,
                                               hashesfor)
        # Sort results by filtered count, descending
        results = results[(-results[:,1]).argsort(),]
        # Where was our best hit in the unfiltered count ranking?
        # (4th column is rank in original list; look at top hit)
        #if np.shape(results)[0] > 0:
        #    bestpos = results[0, 4]
        #    print "bestpos =", bestpos
        # Could use to collect stats on best search-depth to use...

        # Now strip the final column (original raw-count-based rank)
        #results = results[:, :4]

        if hashesfor is None:
            return results
        else:
            id = results[hashesfor, 0]
            mode = results[hashesfor, 2]
            hashesforhashes = self._unique_match_hashes(id, hits, mode)
            return results, hashesforhashes

    def match_file(self, analyzer, ht, filename, number=None):
        """ Read in an audio file, calculate its landmarks, query against
            hash table.  Return top N matches as (id, filterdmatchcount,
            timeoffs, rawmatchcount), also length of input file in sec,
            and count of raw query hashes extracted
        """
        q_hashes = analyzer.wavfile2hashes(filename)
        # Fake durations as largest hash time
        if len(q_hashes) == 0:
            durd = 0.0
        else:
            durd = float(analyzer.n_hop * q_hashes[-1][0])/analyzer.target_sr
        if self.verbose:
            if number is not None:
                numberstring = "#%d"%number
            else:
                numberstring = ""
            print time.ctime(), "Analyzed", numberstring, filename, "of", \
                  ('%.3f'%durd), "s " \
                  "to", len(q_hashes), "hashes"
        # Run query
        rslts = self.match_hashes(ht, q_hashes)
        # Post filtering
        if self.sort_by_time:
            rslts = rslts[(-rslts[:, 2]).argsort(), :]
        return (rslts[:self.max_returns, :], durd, len(q_hashes))

    def file_match_to_msgs(self, analyzer, ht, qry, number=None):
        """ Perform a match on a single input file, return list
            of message strings """
        rslts, dur, nhash = self.match_file(analyzer, ht, qry, number)
        t_hop = analyzer.n_hop/float(analyzer.target_sr)
        if self.verbose:
            qrymsg = qry + (' %.3f '%dur) + "sec " + str(nhash) + " raw hashes"
        else:
            qrymsg = qry

        msgrslt = []
        if len(rslts) == 0:
            # No matches returned at all
            nhashaligned = 0
            if self.verbose:
                msgrslt.append("NOMATCH "+qrymsg)
            else:
                msgrslt.append(qrymsg+"\t")
        else:
            for (tophitid, nhashaligned, aligntime, nhashraw, rank) in rslts:
                # figure the number of raw and aligned matches for top hit
                if self.verbose:
                    msgrslt.append("Matched " + qrymsg + " as "
                                   + ht.names[tophitid] \
                                   + (" at %.3f " % (aligntime*t_hop))
                                   + "s " \
                                   + "with " + str(nhashaligned) \
                                   + " of " + str(nhashraw) + " hashes" \
                                   + " at rank " + str(rank))
                else:
                    msgrslt.append(qrymsg + "\t" + ht.names[tophitid])
                if self.illustrate:
                    self.illustrate_match(analyzer, ht, qry)
        return msgrslt

    def illustrate_match(self, analyzer, ht, filename):
        """ Show the query fingerprints and the matching ones
            plotted over a spectrogram """
        # Make the spectrogram
        d, sr = librosa.load(filename, sr=analyzer.target_sr)
        sgram = np.abs(librosa.stft(d, n_fft=analyzer.n_fft,
                                    hop_length=analyzer.n_hop,
                                    window=np.hanning(analyzer.n_fft+2)[1:-1]))
        sgram = 20.0*np.log10(np.maximum(sgram, np.max(sgram)/1e6))
        sgram = sgram - np.mean(sgram)
        # High-pass filter onset emphasis
        # [:-1,] discards top bin (nyquist) of sgram so bins fit in 8 bits
        # spectrogram enhancement
        if self.illustrate_hpf:
            HPF_POLE = 0.98
            sgram = np.array([scipy.signal.lfilter([1, -1],
                                                   [1, -HPF_POLE], s_row)
                              for s_row in sgram])[:-1,]
        sgram = sgram - np.max(sgram)
        librosa.display.specshow(sgram, sr=sr, hop_length=analyzer.n_hop,
                                 y_axis='linear', x_axis='time',
                                 cmap='gray_r', vmin=-80.0, vmax=0)
        # Do the match?
        q_hashes = analyzer.wavfile2hashes(filename)
        # Run query, get back the hashes for match zero
        results, matchhashes = self.match_hashes(ht, q_hashes, hashesfor=0)
        if self.sort_by_time:
            results = sorted(results, key=lambda x: -x[2])
        # Convert the hashes to landmarks
        lms = audfprint_analyze.hashes2landmarks(q_hashes)
        mlms = audfprint_analyze.hashes2landmarks(matchhashes)
        # Overplot on the spectrogram
        plt.plot(np.array([[x[0], x[0]+x[3]] for x in lms]).T,
                 np.array([[x[1], x[2]] for x in lms]).T,
                 '.-g')
        plt.plot(np.array([[x[0], x[0]+x[3]] for x in mlms]).T,
                 np.array([[x[1], x[2]] for x in mlms]).T,
                 '.-r')
        # Add title
        plt.title(filename + " : Matched as " + ht.names[results[0][0]]
                  + (" with %d of %d hashes" % (len(matchhashes),
                                                len(q_hashes))))
        # Display
        plt.show()
        # Return
        return results

def localtest():
    """Function to provide quick test"""
    pat = '/Users/dpwe/projects/shazam/Nine_Lives/*mp3'
    qry = 'query.mp3'
    hash_tab = audfprint_analyze.glob2hashtable(pat)
    matcher = Matcher()
    rslts, dur, nhash = matcher.match_file(audfprint_analyze.g2h_analyzer,
                                           hash_tab, qry)
    t_hop = 0.02322
    print "Matched", qry, "(", dur, "s,", nhash, "hashes)", \
          "as", hash_tab.names[rslts[0][0]], \
          "at", t_hop*float(rslts[0][2]), "with", rslts[0][1], \
          "of", rslts[0][3], "hashes"

# Run the main function if called from the command line
if __name__ == "__main__":
    localtest()
