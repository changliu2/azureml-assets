Databricks' `dolly-v2-12b`, an instruction-following large language model trained on the Databricks machine learning platform that is licensed for commercial use. Based on `pythia-12b`, Dolly is trained on ~15k instruction/response fine tuning records [`databricks-dolly-15k`](https://github.com/databrickslabs/dolly/tree/master/data) generated by Databricks employees in capability domains from the InstructGPT paper, including brainstorming, classification, closed QA, generation, information extraction, open QA and summarization. `dolly-v2-12b` is not a state-of-the-art model, but does exhibit surprisingly  high quality instruction following behavior not characteristic of the foundation model on which it is based.

Dolly v2 is also available in these smaller models sizes:

* [dolly-v2-7b](https://huggingface.co/databricks/dolly-v2-7b), a 6.9 billion parameter based on `pythia-6.9b`
* [dolly-v2-3b](https://huggingface.co/databricks/dolly-v2-3b), a 2.8 billion parameter based on `pythia-2.8b`

# Evaluation Results
Below you'll find various models benchmark performance on the EleutherAI LLM Evaluation Harness; model results are sorted by geometric mean to produce an intelligible ordering. As outlined above, these results demonstrate that dolly-v2-12b is not state of the art, and in fact underperforms dolly-v1-6b in some evaluation benchmarks. We believe this owes to the composition and size of the underlying fine tuning datasets, but a robust statement as to the sources of these variations requires further study.

model|openbookqa|arc_easy|winogrande|hellaswag|arc_challenge|piqa|boolq|gmean
|--|--|--|--|--|--|--|--|--|
EleutherAI/pythia-2.8b|	0.348|	0.585859|	0.589582|	0.591217|	0.323379|	0.73395|	0.638226|	0.523431
EleutherAI/pythia-6.9b|	0.368|	0.604798|	0.608524|	0.631548|	0.343857|	0.761153|	0.6263|	0.543567
databricks/dolly-v2-3b|	0.384|	0.611532|	0.589582|	0.650767|	0.370307|	0.742655|	0.575535|	0.544886
EleutherAI/pythia-12b|	0.364|	0.627104|	0.636148|	0.668094|	0.346416|	0.760065|	0.673394|	0.559676
EleutherAI/gpt-j-6B|	0.382|	0.621633|	0.651144|	0.662617|	0.363481|	0.761153|	0.655963|	0.565936
databricks/dolly-v2-12b|	0.408|	0.63931|	0.616417|	0.707927|	0.388225|	0.757889|	0.568196|	0.56781
databricks/dolly-v2-7b|	0.392|	0.633838|	0.607735|	0.686517|	0.406997|	0.750816|	0.644037|	0.573487
databricks/dolly-v1-6b|	0.41|	0.62963|	0.643252|	0.676758|	0.384812|	0.773667|	0.687768|	0.583431
EleutherAI/gpt-neox-20b|	0.402|	0.683923|	0.656669|	0.7142|	0.408703| 0.784004|	0.695413|	0.602236

# Limitations and Biases

## Performance Limitations
dolly-v2-12b is not a state-of-the-art generative language model and, though quantitative benchmarking is ongoing, is not designed to perform competitively with more modern model architectures or models subject to larger pretraining corpuses.

The Dolly model family is under active development, and so any list of shortcomings is unlikely to be exhaustive, but we include known limitations and misfires here as a means to document and share our preliminary findings with the community.
In particular, dolly-v2-12b struggles with: syntactically complex prompts, programming problems, mathematical operations, factual errors, dates and times, open-ended question answering, hallucination, enumerating lists of specific length, stylistic mimicry, having a sense of humor, etc. Moreover, we find that dolly-v2-12b does not have some capabilities, such as well-formatted letter writing, present in the original model.

## Dataset Limitations
Like all language models, dolly-v2-12b reflects the content and limitations of its training corpuses.

The Pile: GPT-J's pre-training corpus contains content mostly collected from the public internet, and like most web-scale datasets, it contains content many users would find objectionable. As such, the model is likely to reflect these shortcomings, potentially overtly in the case it is explicitly asked to produce objectionable content, and sometimes subtly, as in the case of biased or harmful implicit associations.

databricks-dolly-15k: The training data on which dolly-v2-12b is instruction tuned represents natural language instructions generated by Databricks employees during a period spanning March and April 2023 and includes passages from Wikipedia as references passages for instruction categories like closed QA and summarization. To our knowledge it does not contain obscenity, intellectual property or personally identifying information about non-public figures, but it may contain typos and factual errors. The dataset may also reflect biases found in Wikipedia. Finally, the dataset likely reflects the interests and semantic choices of Databricks employees, a demographic which is not representative of the global population at large.

# Inference samples

Inference type|Python sample (Notebook)
|--|--|
Real time|[sdk-example.ipynb](https://aka.ms/sdk-notebook-examples)
Real time|[text-generation-online-endpoint.ipynb](https://aka.ms/text-generation-online-endpoint-oss)

# Sample inputs and outputs

### Sample input
```json
{
    "input_data": [
        "I believe the meaning of life is"
    ],
    "params": {
        "top_p": 0.9,
        "temperature": 0.2,
        "max_new_tokens": 100,
        "do_sample": true,
        "return_full_text": true
    }
}
```

### Sample output
```json
[
    "I believe the meaning of life is to find what you love and do that thing until you die. I love to write, code, and spend time with my family. I started this blog to document my learning journey in the tech industry and share things I love with others. I hope you enjoy the content and feel free to leave a comment."
]
```