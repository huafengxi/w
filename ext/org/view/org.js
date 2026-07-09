function parse_org(orgCode)
{
    var orgParser = new Org.Parser();
    var orgDocument = orgParser.parse(orgCode);
    bind(orgDocument.options, {'toc': 0, 'num': 0});
    return orgDocument;
}

function render_org(orgDocument)
{
    var orgHTMLDocument = orgDocument.convert(Org.ConverterHTML, {
        headerOffset: 1,
        exportFromLineNumber: false,
        suppressSubScriptHandling: false,
        suppressAutoLink: false
    });
    return orgHTMLDocument.toString();
}

function lastPartOfSectionNumber(sectionNum) {
    return sectionNum.replace(/.*?([0-9]+)$/, '$1');
}

function supressSectionNumber(pannel) {
    return map(function(node) {node.innerHTML = lastPartOfSectionNumber(node.innerHTML);}, pannel.getElementsByClassName("section-number"));
}
