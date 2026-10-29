# Action Entity execution rulings

This is the exhaustive, ordered record of every progress-log line containing `Ruling:` in `.superpowers/sdd/2026-10-21-action-entity-unification/progress.md` at Task 7 closeout (36 rulings). Each source line is reproduced verbatim, including its stated cost and any later ruling-specific supersession. The chronological progress log remains the source record.

## 1. Progress log ruling

```text
Ruling: retain a distinct existing candidate attempt `result_json` as common optional Action outcome when not byte-equal to provider_result_json — actual source stores local outcome separately; merging into provider response would erase distinct facts — cost if wrong: retaining some compactable local outcome bytes until exact native/source proof. No candidate-specific target layout.
```

## 2. Progress log ruling

```text
Ruling: partial-release coexistence uses one `action` layout inside existing physical Actions storage, with untouched non-Action kinds explicitly transitional — replacing whole table with flat Action-only DDL would erase scope-out records or force unrelated migration; common Action API cannot branch on old origins — cost if wrong: temporary mixed-container/schema overhead until remaining units retire it. Minimal receipt/observer child facts may normalize true many-to-one relationships; no separate source-type workflow and no copied provider body.
```

## 3. Progress log ruling

```text
Ruling: new common Action rows carry no old namespace alias; permit legacy_key SQL NULL only for the common kind and retain equivalent old-kind non-null constraints, excluding common kind from old public-id allocator/index logic — satisfies no-runtime-compatibility while preserving out-of-scope storage — cost if wrong: source triggers/FKs require further precise rewrite before migration can proceed; Task1/2 tests must prove coexistence.
```

## 4. Progress log ruling

```text
Ruling: exact existing external_action_key with matching canonical operation/target/result owner identifies ONE Action even when several source candidate attempts observed it — actual source group has two verified attempts and one236-byte provider result, with distinct local NULL-reference and2-byte TEXT outcomes — keep local outcomes at execution_id/action_index cardinality in minimal Action-owned observation/receipt facts, provider result once. This refines earlier common outcome field ruling; cost if wrong: incorrectly grouping two genuinely different operations, so canonical operation/target identity must match, conflicting group cannot silently merge. Synthetic case required; no source IDs/content committed.
```

## 5. Progress log ruling

```text
Ruling: common Action.status must own reviewed effect current phase as well as email lifecycle; preserve all existing explicit state distinctions with a source-backed union, while per-execution receipt status is immutable history — email-only common status would force source-specific runtime lifecycle back into child receipts — cost if wrong: semantic misnormalization could promote unknown to success or incorrectly retry. Require actual canonical-result writer/reader verification semantics, no arbitrary JSON/timestamp inference; prepared missing phase remainsNULL. Deterministic multi-observation mapping must support newly written fail→verify histories through inverse/reforward.
```

## 6. Progress log ruling

```text
Ruling: preserve historical reference meaning across canonical provider-result correction by immutable owned receipt versions and explicit technical receipt linkage, rather than resolving every observation to latest body — existing sent_result owner/key triggers and preserve_candidate_result_references already preserve old observed body — cost if wrong: extra technical FK/version metadata; without it, receipt correction rewrites history. Added to Task1 fix1 synthetic contract coverage.
```

## 7. Progress log ruling

```text
Ruling: immutable canonical provider receipt corrections use minimal same-Action supersedes_provider_receipt_id lineage and fixed observation.provider_receipt_id; derive canonical current version from chain tip instead of extra mutable is_current flag. Source preserve_candidate_result_references freezes old observed result before canonical correction, so technical version link preserves existing semantics, not a new review policy — cost if wrong: extra FK/index metadata or confusing independent actual receipts with corrections; ordinary email attempts must not be auto-chained as corrections.
```

## 8. Progress log ruling

```text
Ruling: Task2 may add app/action_entity_definitions.py as pure target DDL/field declaration shared by runtime and inspector; keep source declaration as explicit source factory for migration/inverse, avoiding current_action_objects→tool→current_action_objects recursion and runtime docs/tool imports — cost if wrong: one extra small definition module; require target artifact equality tests and exact auxiliary-table schema checks. This is implementation organization only; no source-format runtime dispatch.
```

## 9. Progress log ruling

```text
Ruling: Task2 adds two explicit common child-fact helpers in same owner module: add_action_observer(db,action_id,*,agent_run_id,task_id,created_at)->None and append_action_receipt(db,action_id,*,values:Mapping[str,object])->int. Four required parent APIs/return fieldset remain unchanged; caller owns transaction, fixed receipt allowlist, no origin routing or parent status inference — actual many-to-one facts require writes, and embedding child facts in parent DTO would revive duplication — cost if wrong: two extra small storage entrypoints; preserve existing duplicate/boundary semantics with tests.
```

## 10. Progress log ruling

```text
Ruling: Task3 may modify narrowly scoped prepared Action projection in action_storage.py, absent its nominal filelist but required by real Store prepare/get/claim/receipt/readback callers. Read from commonAction/ownedreceipts only, no old-record union/fallback/origin dispatch; Task4 retires obsolete projection DTO names when consumers converted — cost if wrong: Task3 reader scope expansion or temporary oldname survives; reviewer must verify actual chain and Task4 exit list.
```

## 11. Progress log ruling

```text
Ruling: Task3 supporting reviewed runtime reads CANDIDATE_ACTION_ATTEMPT_SQL/EXTERNAL_ACTION_RESULT_SQL in action_storage.py switch to commonAction/receipt joins with real writer conversion, preserving plan/executionowners. Directemail caller-local common projections in email_store.py allowed; shared unsubscribe readers remain for genuinely different outscopeworkflow, never old-origin fallback. Distinguish Action id and execution-observationreceiptid — cost if wrong: older observation/reference overwritten or partial retirement hidden; report exact remaining sharedcaller scope and Task4followups.
```

## 12. Progress log ruling

```text
Ruling: reviewed observation cardinality follows real source one current row per execution_id/action_index (dispatched→verified updates mutable status/outcome/clocks), not new permanent per-transition eventlog. Immutable provider receipt versions and established exact references remain; different executions retain independent observations. Earlier Task3checkpoint dispatched/verifiedhistory assertion must be corrected — cost if wrong: loss of a truly distinct source fact; verify old uniqueness/source writer and preserve prior executions, no blanket receipt-history deletion. This avoids new unnecessary history contrary to simplification goal.
```

## 13. Progress log ruling

```text
Ruling: correct frozenreceiptDDL missedsourceconstraint: replace nonunique execution-orderindex with UNIQUE(execution_id,action_index) WHERE execution_id IS NOT NULL, matching actualcandidate_action_attempts slotuniqueness. Update runtime/tool/staticDDL/indexrationale+synthetic rejectiontests together, preserveNULLexecutionprovider versions as separatefacts — cost if wrong: false uniqueness could reject truly distinct observations; sourceconstraint evidence and same/differentAction duplicate tests required, Task3reviewcoverschange. No addedworkflowpolicy.
```

## 14. Progress log ruling

```text
Ruling: Task3 must retarget existing direct-email in-flight SQLblockers to commonAction classification/account/status fields with exactsame condition/message; otherwise sourceplan/account canchange whileprocessing. Target-only declaration composition permitted in guardDDL files; historical action_email_storage._target_external_sql/finite_target_external_sql factories/fingerprints muststaybyteequal for explicitmigration/inverse — cost if wrong: existingbusinessprotection weakened or historicalsourcerecognition broken; focusedcurrentprocessingblocker regression and oldfactory equality required. No newreview/safety policy.
```

## 15. Progress log ruling

```text
Ruling: Sent-only→canonical adoption may bind initially-NULL canonicalmetadata/digest once from existingverifiedSent+boundplan, thereafterimmutable. Mustrecord oldsource canonicalSHA/createdclock andpreserve separateSent/preparedobservedclocks viaexistingreceiptfields/actualobserverfacts+bodyreference; no secondproviderbody or fakehistory. FrozenDDL/mapping correctionrequires sourceevidence+tests/review — cost if wrong: ordinaryproviderfact mislabeledcanonical orclockloss; canonicalquerymustselectrealadoptedmetadata, not infer successfrombody/digestalone. No newcallereligibilitygate.
```

## 16. Progress log ruling

```text
Ruling: source correction to earlier blanket immutableproviderref rule — providerbodyversions/id/owner/lineage immutable; current execution observation provider_receipt_id may CAS old→new sameAction under existingexplicitreverification caller. Passivecanonicalcorrection alone leaves other observations boundoldversion. Source apply_delivery_reverification updatescurrentattempts explicitly aftercanonicalwrite; preserveonecurrentrow/slot — cost if wrong: historical refs advance unexpectedly or legitimate recovery blocked; tests passiveoldref vs explicitscopedadvance, oldbody preserved required. Earlier blanketfreeze supersededonlyforactualmutablecurrentobservations, notcanonicalmetadata refs.
```

## 17. Progress log ruling

```text
Ruling: add minimal technical Action.delivery_receipt_id INTEGER ownedreceipt reference, sourcedoldSent.id/send_result_json/sent_at relation, to identify currentSent fact withoutfakekind/operation or guessing newestbody. OneSentfactreceipt owns originalsentclock andexplicitlymutablecurrentproviderref; providerbody storedonce. OldexplicitSentrepair loops must CASthatref, notdeletedrelyingautoprojection — cost if wrong: oneextraFK/JSONscalar or incorrectSentmapping; sourcebinding/clock/passivevsactivecorrection tests and exactDDL/fieldmap required. No newbusinesscolumn orsendpolicy.
```

## 18. Progress log ruling

```text
Ruling: preserve source directemail UNIQUE(action_plan_id,action_type) via commonunique partialindex(action_action_plan_id,action_operation) forAction/nonNULLplan ratherthanpersistingderivedoldhashalias in2fields. Oldhash reconstructedonlyexplicitinverse, runtimeusesphysicalid — cost if wrong: oneextra compactpairindex ormisidentifiedplan-slot; sourceuniqueproof +stale/concurrent/retryidentity tests +DDL/artifactupdate required.
```

## 19. Progress log ruling

```text
Ruling: correct Task3/4 acceptance-boundary contradiction in plan/brief: preserve entire existing mixed writer+History test, Task3 must execute/report it but full green combinedreader acceptance belongsTask4 after actualreaderconversion. Task3 writer scope can be reviewed independently withexplicitpendingreader requirement; nevermark wholephaseorcombinedsuiteacceptedbeforeTask4green. No runtime behavior/assertion removal. Fourlistedfiles need actualcaller/source no-change evidence, notcosmeticcodehunks. Cost ifwrong: a realwriter defect could be mislabeledreader; reviewer mustvalidate failurelayer andunchangedcallgraph, Task4exactnode/fullprescribedgroup remain mandatory.
```

## 20. Progress log ruling

```text
Ruling: amend Task4filelist toinspectactualHistoryhelpers andmodifyonlystorage-owningcallpaths, sourceevidence required forunchangedfiles. Focusedrootread shows history.py safe_observability_error formatting, history_actions.py attention/policyfunctions operatepassedReplyAttempt/HistoryItem notdatabaseprojection; modifyingthempurelyforfilecheckwouldriskunapprovedpolicychange. ActualqueriesownedStore/audit_web areconverted. Costifwrong: missedreaderhiddeninhelper; fixreportmusttraceexactinputs/callers andTask1consumerinventory, scopedreviewverifiesevidence. AuthenticcopyUIremainsTask6releasegate,nosyntheticreplacement.
```

## 21. Progress log ruling

```text
Ruling: coveredActionHistoryusesactualcommonActionowner/ID, semanticEmailActionhistorylabelmayremainforbusinessfilterpresentation butmustnotneedoldemail_actions storagealias orlegacyIDprefix. Traceconsumer/filtermappingandconvertcoverednormalruntimealiases; explicitmigration/sourcefactoriesremain. Costifwrong: publicHistoryIDs/filtermetadata change, userexplicitlydoesnotrequireoldstructurecompat; preservesemanticEmailfilter/status/detailURLandpagination tests. No audit/retry/authorizationchanges. AdditionalfixsameTask4round1, focusedtestsrequiredforruntimechange.
```

## 22. Progress log ruling

```text
Ruling: Task5normalizedinverse-only action_entity_identity_map(source_type TEXT,source_identity TEXT,action_id INTEGER FK,receipt_id nullable,PKsource_type+source_identity) accepted withinapprovedschema+typedidentitymappingmetadata, no business/providerbodycopy andno normalruntimeconsumers. Oldpublicnamespacesoverlap; map preservesexplicitinverseidentitywithoutaliasesinAction. StateKVcanhold30kJSONvalues; normalizedchoicefornativeuniqueness/ownerreferences andavoidlargeopaque businessstatevalue, notfalsecapacityclaim. Technicalfieldsources/typedencoding/receiptownershipmustdocument+test/staticDDL. Costifwrong: oneadditionaltechnicaltable/indices andestimated1-4MB(mustmeasureTask6), notadditionalbusinessmodel; canreconsideraftermeasuredcost, noextraeffect/policy. Unsupportedinverseguardcannotrejectlegitimatecoverednewreviewed/direct/preparedfacts; requireallreceipts/observations/observer/recall/historylist notSentalone.
```

## 23. Progress log ruling

```text
Ruling: add minimal nullableAction.prepared_receipt_id ownedreceipttechnicalreference: sourceoldoutbound_postfix_receipts unique(channel,delivery_key) actualfact muststayindependentfromSent/canonicalJSON onmergedAction. ConfirmedTask3getreceiptprojectionreturnsallproviderbodiesandwriterfirstANYbody; cannotdependinsertionorder. NewpointeranalogousSentdeliverypointer, no subtype/operationflag/bodydup ornewpolicy; sourceclockbelongsreceipt. UpdatefrozenDDL/map/commonfieldset/ownerguards/writer/read/tests includingmergeddistinctbodies/crossowner/inverse/reforward. Preservemissingparent/orphanfacts nofabricatedpreparationtime/body. Costifwrong: oneextra nullableinteger+FKguard/JSONkey(28thfield), test/publishsurface expands; necessaryprecisesourcebindingavoidwrongreceipt/replay.
```

## 24. Progress log ruling

```text
Ruling: partitionload-bearingTask5 intoboundedfreshLuna steps ratherthancontrollercoding/modelupgrade. FirstTask5A realoneeffectbinding; thenTask5B populatedforward, Task5C currentdatainverse/coldold/reforward, allunderTask5acceptance. Currentbegin_reviewed_actionexternalkey-onlycreatesseparateownerfromearlierpreparedkeyowner; needrealtestconfirm andsourcebackedbindingbeforeclaim. Source actualkeyderivation existsConsumer/SystemMessageHandler candidate/selection/revision/generation, nobodyheuristic/newpolicy. Costifwrong: extra internalreview/dispatchoverhead andruntimecoupling scope, but avoidsimplementingwrong rowmerger or maskingduplicateactualeffects; no usermodel/costswitch.
```

## 25. Progress log ruling

```text
Ruling: correct Task3 over-retention/currentreconciliation divergence using genuine897 predecessor semantics. Distinct ACTUAL provider receipts and source-observable attempt/Sent facts remain retained; unreferenced intermediate verification wrappers are mutable currentcanonical state, not new permanent receipt history. Old record_candidate_external_action accepts ambiguous->sent only unchanged nestedprovider_result, materializes exacteligibleverifiedSQLNULLattempts thenupdatescanonical; deliveryrepair sameprinciple. Old record_prior_candidate_action_reconciliation insertscanonicalonlyifabsent; mismatchwithexistingcanonical stayslocalinthatrealattempt, neverreplacescanonical. Task5C must restore these semantics and reconcile frozenimmutability/lineage wording+DDLtests, notuseunrepresentable inverseguard orinventoldslots. Supersedesmetadata onlywhereactualsource-observablepriorfact needsreference; no lossdistinctactualreceipt orotherexecutionobservation. Costifwrong: intermediatewrapper/timestamps no longerpermanent and source-referenceprojectioncoulddrift; exactold-vs-current correction/localresult/NULLSentfollowing/attemptmaterialization regression+currentdatainverse requiredbeforedeploy. Earlier blanketallwrapperversionsimmutablechain rulings supersededonlyfornon-distinct source-overwritten wrapperstate; trueactualreceiptbody/owner/identity remainsimmutable.
```

## 26. Progress log ruling

```text
Ruling: Task5A binds exactpreparationowner foractualnewproviderdispatch; a NEWrevision/generation mayhaveunusedpreparedfactforalreadycompletedcanonicaleffect (deliverykey includesrevision/generation,externalkeydoesnot). Such unusedpreparation remainsseparateunexecutedAction, newexecutionobservesexistingcanonicalowner andmustnotresend ornewownershipconflictgate. Actualoneeffectstilloneexecutedowner; do notmergeunuseddistinctpreparationbody merelytounifyIDs. Costifwrong: additionalunexecutedpreparedrowretained andbindingconditionnuance; realfirstsend+newapprovedrevisionreuse testmustshowprovider1/existingcanonicalowner/currentexecutionref, noinventedhistory orpolicy.
```

## 27. Progress log ruling

```text
Ruling: correct controller/readonlydesigner genericNULLSent inference withactualsent_result_definitions.py27-37,50-66. Provenkey+equaltype/value localSentbody isauto-normalizedNULL bysourceAFTERtriggers; oncanonicalupdate/delete sourceAFTERtriggermaterializesOLDbody beforeexplicitrepairadoptsnewreceipt. PassiveoldSentreferencepreservation earlierTask3rulingstands. TestvaliddifferentlocalbodyvsNULL, notdisabled-triggerstatic-equalasnormalacceptance. Costifwrong: sourcecorrectionsemanticscouldbefrozen/advancedincorrectly; exactoldtrigger/sourcefixture/regression+currentinverseproof required. No newpresenceflags/dynamiclogicalrootrequired; preserveactualindependentbodies, removeonlyunreferencedsource-overwrittenwrapperhistory perpriorruling. Coordinator5Chandoff/designnotescorrected, earliergenericfollows wording superseded.
```

## 28. Progress log ruling

```text
Ruling: Task5B actualcopyfound2survivingemail_direct_retry_requests withFK33 actions(email_operation_parent_type,email_action_id)->actions(record_type,email_public_key), stillpointingretiredemail_actions. Rewrite ONLYtheiractualaction_idreference tocommonphysicalActionID and retarget generatedFK/typeprotection toactualcommonAction parent; preserveexistingretrypolicy/othercontrolrecordfields/identities. Updatefrozenfieldmap/targetDDL/indexrationale+currentactualretrywriter/readerifneeded, sourcefactorybyteexactunchanged. Explicitinverse restoresoldemailIDbytypedmap/deterministicplan-operationformula. Allotheroutscopephysicalcellsbyteexact; thisverifiedFKcellisnecessarymappedexception, notlicensebroadreencoding. Nofakelegacyowner, runtimeoldhashaliases/identitymaplookup, ornewbusinessfield. Costifwrong: narrowcontrolrefcontract/DDLscopeexpandsandcouldmisroutependingretry; oldactive2/newcurrentretryfixtures+read/claimreferences+forward/inverse/sourceprotectiontests required. CommonFKusesrealphysicalActionidentity, sourceroleprotectionmustremainwithoutnewretry/auth/reviewpolicy. ThisresolvesplanliteraloutscopebytepreservationvsnecessarycoveredparentFKrewrite usingbindingactualidentitycontract.
```

## 29. Progress log ruling

```text
Ruling: precise2retryparentreference exception is legacy_key composite element0, notseparatepayloadfield; currenttargetlegacy_key changedONLYparentcomponent tocommonphysicalActionID, othercomponents/payload/status/clocks/sourcephysicalrowIDexact. Thisisnecessaryinternalparentnamespaceconversion, notlossindependentbusinessrequestidentity; oldinternalkeysnotpermanentcompatcontract andinversemap/formularestoresoldparentcomponent. Costifwrong: hiddenlinksusingwholecompositecouldbreak; currentnewretryAPI+sourceFK/othercellproof andinverse2old/newretryroundtriprequired. Earlier'otheridentitiesunchanged' phraseappliestoindependentfacts/components, notparentIDembeddedinthiscomposite.
```

## 30. Progress log ruling

```text
Ruling: promoteTask5BreviewMinorhistorical-sourcecase torequiredacceptancefix: currenttestseedsAFTERpublishandcalls_transfer_emaildirectly, skipsSOURCEcapture/fulltransaction despitebriefexplicitgenuineoldhistoricaloperation+activeactions. Addvalidoldsourcebeforeprepare/publish, preserveorderedIDreference/state/immutabletuple. Costifwrong: extra focusedfixturework, butactualsource0histrowscannotproveproductionfuturehistorysourcecapture; thiscloseslatentdeploydata-shapehole. REDtraceunsaved/inlinefullverifiergapremainsdocumentedMinorforTask6reproducibleharness, neverreconstructhistoriclogs.
```

## 31. Progress log ruling

```text
Ruling: C1canonical current version uses one stable reference-only canonical metadata receipt with existing operation/target/result_digest/agent_run_id/created_at and mutable provider_receipt_id; identify through actual complete canonical tuple, no execution/action_index/attempt_number, exclude actual Sent/prepared receipt IDs. Preserve first canonical metadata there; Action.agent_run_id remains actual first Sent sender, no canonical aliases/new businessfields. CANONICAL query follows this authoritative pointer directly. Other exact execution/Sent body pointers remain unchanged, source inverse materializes them only when different from current canonical. Fresh forward maps external_action_results identity to stable canonical metadata, not transientbody. Prune only specifically replaced verification-wrapper body with no independent exact refs; neverglobalGC/distinctproviderloss/runtimeidentitymaplookup. UnreleasedpreC1targetcopy is evidence only; nextcopyfreshforwards source, no runtimecompatrequired. Supersededwrapperchain technicalfield/index/recursiveprojection no longerneeded and shouldretire fromcurrentcontract/callers/tests/docs, preserving immutable actualbody andsameownerFKprotections. Costifwrong: extra small canonicalbindingrow wherepreviousSentpiggybacked, bindingidentification/firstclock coulddrift or referencedbodylost; actualsource-before/after mismatch+sameattemptcorrection+otherexecution/Sentstatic+explicitrepair+firstclocks regressions, frozenDDL/manifest andlatercurrentdatainverse mandatory. This supersedes earlier blanket successor-chain requirement only for currentcanonical versioning; trueindependentactualproviderreceipts stayretained. No new audit/auth/retry/reconciliationpolicy.
```

## 32. Progress log ruling

```text
Ruling: refineC1currentdigestownership: read current result_digest fromexactcurrentbodyreceipt, notmutate reference-only firstmetadata digest merelytofollowbody. Existingfirstsourcecanonicaltuple staysstable; report/projectionmustshowcurrentdigestisbody-derived. Costifwrong: stale fallbackdigest couldmaskmissingcurrentbodydigest; actualbody-required/currentdigest regression andinversecurrentcanonicalreadbackmustproveownership, neverrelax immutableactualbody. FivefocusedC1regressionsworkerreported15.64s; caller/DDL/storagefullcoveringchecks+reviewpending.
```

## 33. Progress log ruling

```text
Ruling: C2genuineRED/GREENverificationmayuseexplicitlylabeledNEWcontrolledexactbase-v-current comparison ofsamelegitimatecurrentwriterfacts, matchingC1acceptedproof, whilehistoricalinitialloggapremainsdisclosed. Cannotrecoveroriginalcommand/stderr andmustnotfabricatechronology. Negativeproofmustreachactualinverseconversion/observablebusinessfailure, notmissingtest-privatehelper/fixture/importlayer. Costifwrong: a fixture/pathdifferencecouldfakeREDwithouttestinginverse; preserveexactoverlay/app/fixtureimporthashes/commands/assertionlayer andindependentscopedreview, no production/nativeclaim. Thisclarifiesverificationevidenceonly, no runtime/policychange.
```

## 34. Progress log ruling

```text
Ruling: Task6 same-population capacity comparator uses exact typed predecessor identity (_kind,legacy_key) for covered retired Action families, normalizes ONLY their physical actions.id and actual sent_reply_observers.sent_reply_id resolved through unique typed Sent identity; all other stored cells/SQLtypes, uncovered physicalIDs, keysets/counts/allocators/FK findings remain strict. Actualinverse rebuilds covered physicalcontainers and contracts retain typed publicidentity, so whole-rowid digest falsely compared storage placement as businessidentity. Evidence: worker102804 unique identitieseach,0keydelta/duplicates, allstoredfields exactexcept27915coveredrowidremaps+487observerrefs; referencedSentidentity487/487equal/no unresolved SHA399d5aff6cf1b8d8fb88e7fcb23573e34960429fc66f40413b22ffef9fa259ad; rootindependentreadonly remapcounts email_actions11387/email_action_attempts13478/Sent2508/prepared55/observers487, outscope0, sequencesexact. Costifwrong: normalizationcouldmaskbrokenparentlink/identitychange; comparator must assert uniqueproperfamily refs and exactoldtypedidentitysets/outscopephysicalIDs, adversarial changedbody/wrongSent/missingref tests required and independentreview. No runtimecompatibility/maplookup/newbusinessfield or schema change.
```

## 35. Progress log ruling

```text
Ruling: clarifyTask6typedsemanticphysicalIDnormalization uses actualRETIRED_ACTION_TYPES declaration (all9coveredfamilies), notA1hardcoded5whichmerelyobservedoldcohortremaps. Newlycreatednativeeffectpermits44candidateattemptcoveredcontainerIDremaps; originalbindingrulingalreadycoveredretiredfamilies. Rootindependentreadonlyafter-old-coldvsreforward-inverse-check task584... keysetsEqualTrue/allotherstoredcells+typesEXACT(excludingid/sent_reply_idonly), remapscandidate44/prepared1241/Sent2509/email11387/emailattempt13478/observer488,outscope0. Observerreferenceidentity/wholecohort/sequence/FKstillmustpass, uncoveredphysicalIDsremainstrict, no genericIDignore. Costifwrong: treatingtrulyexternalphysicalIDcontractasinternalcouldhidebrokenlinks; keep typedpublicidentity+allrefs/negativeoutscopeIDtests andderiveactualretireddeclaration, independentreviewrequired. No runtimecontract/schema/policychange.
```

## 36. Progress log ruling

```text
Ruling: post-final-review predeployment inspection found repository_updater.in_flight_work still counts literal retired email_actions/email_status; existing publication_email_action_source alreadyprovides correctcurrentcommonActionstatuses. Treat as separately scoped load-bearing migration/deploymentreader omission, preserveexactquietpolicy byreusingexistingpublicationtoolsource, add genuinecurrentprocessingclaimREDregression andscopedindependentreview BEFOREfinalreleasedHEAD. Prioronefinalfixwave rule cannotjustifyshippingknownmissedclaimcount; userauthorizescompleteverifiedmigration, thisadditionalunitdoesnotalterauth/audit/safetydecisionrule. Costifwrong: releasecouldmiscountunrelatedActions/mixedfreshsourceorintroducenewquietsemantics; sourcepair/currentprocessing/nonprocessing/freshStore-only regressions+strictoperationfilter andbeforeafterproofrequired. Standarddeploycurrentlywaitingoldsourcequiet1; no prodchange/no bypass/nohandkill.
```
